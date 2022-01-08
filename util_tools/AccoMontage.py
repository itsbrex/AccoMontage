import numpy as np 
from tqdm import tqdm
import pandas as pd 
import torch

import sys
sys.path.append('./util_tools')
from acc_utils import melodySplit, chordSplit, computeTIV, chord_shift, cosine, cosine_rhy, accomapnimentGeneration

sys.path.append('./models')
from model import DisentangleVAE

import os
os.environ["KMP_DUPLICATE_LIB_OK"]="TRUE"




def find_by_length(melody_data, acc_data, chord_data, length):
    melody_record = []
    acc_record = []
    chord_record = []
    song_reference = []
    for song_idx in tqdm(range(acc_data.shape[0])):
        for phrase_idx in range(len(acc_data[song_idx])):
            melody = melody_data[song_idx][phrase_idx]
            if not melody.shape[0] == length * 16:
                continue
            if np.sum(melody[:, :128]) <= 2:
                continue
            melody_record.append(melody)
            acc = acc_data[song_idx][phrase_idx]
            acc_record.append(acc)
            chord = chord_data[song_idx][phrase_idx]
            chord_record.append(chord)
            song_reference.append((song_idx, phrase_idx))
    return np.array(melody_record), np.array(acc_record), np.array(chord_record), song_reference

def dp_search(query_phrases, seg_query, acc_pool, edge_weights, texture_filter=None, filter_id=None, spotlights=None):
    print('Searching for Phrase 1')
    query_length = [query_phrases[i].shape[0]//16 for i in range(len(query_phrases))]
    mel, acc, chord, song_ref = acc_pool[query_length[0]]
    mel_set = mel
    rhy_set = np.concatenate((np.sum(mel_set[:, :, :128], axis=-1, keepdims=True), mel_set[:, :, 128: 130]), axis=-1)
    query_rhy = np.concatenate((np.sum(query_phrases[0][:, : 128], axis=-1, keepdims=True), query_phrases[0][:, 128: 130]), axis=-1)[np.newaxis, :, :]
    rhythm_result = cosine_rhy(query_rhy, rhy_set)

    chord_set = chord
    chord_set, num_total, shift_const = chord_shift(chord_set)
    chord_set_TIV = computeTIV(chord_set)
    query_chord = query_phrases[0][:, 130:][::4]
    query_chord_TIV = computeTIV(query_chord)[np.newaxis, :, :]
    chord_score, arg_chord = cosine(query_chord_TIV, chord_set_TIV)
    score = .5*rhythm_result + .5*chord_score
    if not spotlights == None:
        for spot_idx in spotlights:
            for ref_idx, ref_item in enumerate(song_ref):
                    if ref_item[0] == spot_idx: 
                        score[ref_idx] += 1

    if not filter_id == None:
        mask = texture_filter[query_length[0]][0][filter_id[0]] * texture_filter[query_length[0]][1][filter_id[1]] - 1
        score += mask

    #print(np.argmax(score), np.max(score), score[0])
    path = [[(i, score[i])] for i in range(acc.shape[0])]
    shift = [[shift_const[i]] for i in arg_chord]
    melody_record = np.argmax(mel_set, axis=-1)
    record = []

    for i in range(1, len(query_length)):
        print('Searching for Phrase', i+1)
        mel, acc, chord, song_ref = acc_pool[query_length[i]]

        weight_key = 'l' + str(query_length[i-1]) + str(query_length[i])
        contras_result = edge_weights[weight_key]
        #contras_result = (contras_result - 0.9) * 10   #rescale contrastive result if necessary
        #print(np.sort(contras_result[np.random.randint(2000)][-20:]))
        if query_length[i-1] == query_length[i]:
            for j in range(contras_result.shape[0]):
                contras_result[j, j] = -1   #the ith phrase does not transition to itself at i+1
                for k in range(j-1, -1, -1):
                    if song_ref[k][0] != song_ref[j][0]:
                        break
                    contras_result[j, k] = -1   #ith phrase does not transition to its ancestors in the same song.
        #contras_result = (contras_result - 0.99) * 100
        if i > 1:
            contras_result = contras_result[[item[-1][1] for item in record]]

        if not spotlights == None:
            for spot_idx in spotlights:
                for ref_idx, ref_item in enumerate(song_ref):
                    if ref_item[0] == spot_idx:
                        contras_result[:, ref_idx] += 1

        mel_set = mel
        rhy_set = np.concatenate((np.sum(mel_set[:, :, :128], axis=-1, keepdims=True), mel_set[:, :, 128: 130]), axis=-1)
        query_rhy = np.concatenate((np.sum(query_phrases[i][:, : 128], axis=-1, keepdims=True), query_phrases[i][:, 128: 130]), axis=-1)[np.newaxis, :, :]
        rhythm_result = cosine_rhy(query_rhy, rhy_set)
        chord_set = chord
        chord_set, num_total, shift_const = chord_shift(chord_set)
        chord_set_TIV = computeTIV(chord_set)
        query_chord = query_phrases[i][:, 130:][::4]
        query_chord_TIV = computeTIV(query_chord)[np.newaxis, :, :]
        chord_score, arg_chord = cosine(query_chord_TIV, chord_set_TIV)
        sim_this_layer = .5*rhythm_result + .5*chord_score
        if not spotlights == None:
            for spot_idx in spotlights:
                for ref_idx, ref_item in enumerate(song_ref):
                    if ref_item[0] == spot_idx: 
                        sim_this_layer[ref_idx] += 1
        score_this_layer = .7*contras_result +  .3*np.tile(sim_this_layer[np.newaxis, :], (contras_result.shape[0], 1)) + np.tile(score[:, np.newaxis], (1, contras_result.shape[1]))
        melody_flat =  np.argmax(mel_set, axis=-1)
        if seg_query[i] == seg_query[i-1]:
            melody_pre = melody_record
            matrix = np.matmul(melody_pre, np.transpose(melody_flat, (1, 0))) / (np.linalg.norm(melody_pre, axis=-1)[:, np.newaxis]*(np.linalg.norm(np.transpose(melody_flat, (1, 0)), axis=0))[np.newaxis, :])
            if i == 1:
                for k in range(matrix.shape[1]):
                    matrix[k, :k] = -1
            else:
                for k in range(len(record)):
                    matrix[k, :record[k][-1][1]] = -1
            matrix = (matrix > 0.99) * 1.
            #print(matrix.any() == 1)
            #print(matrix.shape)
            score_this_layer += matrix
        #print(score_this_layer.shape)
        #print('score_this_layer:', score_this_layer.shape)
        topk = 1
        args = np.argsort(score_this_layer, axis=0)[::-1, :][:topk, :]
        #print(args.shape, 'args:', args[:10, :2])
        #argmax = args[0, :]
        record = []
        for j in range(args.shape[-1]):
            for k in range(args.shape[0]):
                record.append((score_this_layer[args[k, j], j], (args[k, j], j)))
        
        shift_this_layer = [[shift_const[k]] for k in arg_chord]

        new_path = [path[item[-1][0]] + [(item[-1][1], sim_this_layer[item[-1][1]])] for item in record]
        new_shift = [shift[item[-1][0]] + shift_this_layer[item[-1][1]] for item in record]

        melody_record = melody_flat[[item[-1][1] for item in record]]
        path = new_path
        shift = new_shift
        score = np.array([item[0] for item in record])

    arg = score.argsort()[::-1]
    return [path[arg[i]] for i in range(topk)], [shift[arg[i]] for i in range(topk)]

def render_acc(pianoRoll, chord_table, query_seg, indices, shifts, acc_pool):
    acc_emsemble = np.empty((0, 128))
    for i, idx in enumerate(indices):
        length = int(query_seg[i][1:])
        shift = shifts[i]
        acc_matrix = np.roll(acc_pool[length][1][idx[0]], shift, axis=-1)
        acc_emsemble = np.concatenate((acc_emsemble, acc_matrix), axis=0)
    #print(acc_emsemble.shape)
    acc_emsemble = melodySplit(acc_emsemble, WINDOWSIZE=32, HOPSIZE=32, VECTORSIZE=128)
    chord_table = chordSplit(chord_table, 8, 8)
    #print(acc_emsemble.shape, chord_table.shape)
    pianoRoll = melodySplit(pianoRoll, WINDOWSIZE=32, HOPSIZE=32, VECTORSIZE=142)

    model = DisentangleVAE.init_model(torch.device('cuda')).cuda()
    checkpoint = torch.load('./data files/model_master_final.pt')
    model.load_state_dict(checkpoint)
    pr_matrix = torch.from_numpy(acc_emsemble).float().cuda()
    #pr_matrix_shifted = torch.from_numpy(pr_matrix_shifted).float().cuda()
    gt_chord = torch.from_numpy(chord_table).float().cuda()
    #print(gt_chord.shape, pr_matrix.shape)
    est_x = model.inference(pr_matrix, gt_chord, sample=False)
    #print('est:', est_x.shape)
    #est_x_shifted = model.inference(pr_matrix_shifted, gt_chord, sample=False)
    midiReGen = accomapnimentGeneration(pianoRoll, est_x, 120)
    return midiReGen
    #midiReGen.write('accompaniment_test_NEW.mid')

def ref_spotlight(ref_name_list):
    df = pd.read_excel("./data files/POP909 4bin quntization/four_beat_song_index.xlsx")
    check_idx = []
    for name in ref_name_list:
        line = df[df.name == name]
        if not line.empty:
            check_idx.append(line.index)#read by pd, neglect first row, index starts from 0.
    #print(check_idx)
    for name in ref_name_list:
        line = df[df.artist == name]
        if not line.empty:
            check_idx += list(line.index)#read by pd, neglect first row, index starts from 0
    return check_idx


def get_texture_filter(acc_pool):
    texture_filter = {}
    for key in acc_pool:
        acc_track = acc_pool[key][1]
        #CALCULATE HORIZONTAL DENSITY
        onset_positions = (np.sum(acc_track, axis=-1) > 0) * 1.
        HD = np.sum(onset_positions, axis=-1) / acc_track.shape[1]
        simu_notes = np.sum((acc_track > 0) * 1., axis=-1)
        VD = np.sum(simu_notes, axis=-1) / (np.sum(onset_positions, axis=-1) + 1e-10)
        dst = np.sort(HD)
        HD_anchors = [dst[len(dst)//5], dst[len(dst)//5 * 2], dst[len(dst)//5 * 3], dst[len(dst)//5 * 4]]
        HD_Bins = [
        HD < HD_anchors[0], 
        (HD >= HD_anchors[0]) * (HD < HD_anchors[1]), 
        (HD >= HD_anchors[1]) * (HD < HD_anchors[2]), 
        (HD >= HD_anchors[2]) * (HD < HD_anchors[3]), 
        HD >= HD_anchors[3]
        ]

        dst = np.sort(VD)
        VD_anchors = [dst[len(dst)//5], dst[len(dst)//5 * 2], dst[len(dst)//5 * 3], dst[len(dst)//5 * 4]]
        VD_Bins = [
        VD < VD_anchors[0], 
        (VD >= VD_anchors[0]) * (VD < VD_anchors[1]), 
        (VD >= VD_anchors[1]) * (VD < VD_anchors[2]), 
        (VD >= VD_anchors[2]) * (VD < VD_anchors[3]), 
        VD >= VD_anchors[3]
        ]
        texture_filter[key] = (HD_Bins, VD_Bins)
    return texture_filter