# Copyright 2018 Stanford University
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""This file contains code to read tokenized data from file,
truncate, pad and process it into batches ready for training"""

from __future__ import absolute_import
from __future__ import division

import random
import time
import re

import numpy as np
from six.moves import xrange
from vocab import PAD_ID, UNK_ID

from nltk import pos_tag, ne_chunk, FreqDist
from nltk.chunk import tree2conlltags
from nltk.stem import WordNetLemmatizer
from nltk.corpus import wordnet

class Batch(object):
    """A class to hold the information needed for a training batch"""

    def __init__(self, context_ids, context_mask, context_tokens, qn_ids, qn_mask, qn_tokens, ans_span, ans_tokens, feats, char_ids, char_mask, commonQ_mask, commonQ_emb_indices, charQ_ids, charQ_mask, commonC_mask, commonC_emb_indices, uuids=None):
        """
        Inputs:
          {context/qn}_ids: Numpy arrays.
            Shape (batch_size, {context_len/question_len}). Contains padding.
          {context/qn}_mask: Numpy arrays, same shape as _ids.
            Contains 1s where there is real data, 0s where there is padding.
          {context/qn/ans}_tokens: Lists length batch_size, containing lists (unpadded) of tokens (strings)
          ans_span: numpy array, shape (batch_size, 2)
          uuid: a list (length batch_size) of strings.
            Not needed for training. Used by official_eval mode.
        """
        self.context_ids = context_ids
        self.context_mask = context_mask
        self.context_tokens = context_tokens

        self.qn_ids = qn_ids
        self.qn_mask = qn_mask
        self.qn_tokens = qn_tokens

        self.ans_span = ans_span
        self.ans_tokens = ans_tokens

        self.uuids = uuids

        self.batch_size = len(self.context_tokens)

        self.feats = feats

        self.char_ids  = char_ids
        self.char_mask = char_mask

        self.charQ_ids  = charQ_ids
        self.charQ_mask = charQ_mask

        self.commonQ_mask = commonQ_mask
        self.commonQ_emb_indices = commonQ_emb_indices

        self.commonC_mask = commonC_mask
        self.commonC_emb_indices = commonC_emb_indices

def get_wordnet_pos(treebank_tag):

    if treebank_tag.startswith('J'):
        return wordnet.ADJ
    elif treebank_tag.startswith('V'):
        return wordnet.VERB
    elif treebank_tag.startswith('N'):
        return wordnet.NOUN
    elif treebank_tag.startswith('R'):
        return wordnet.ADV
    else:
        return ''

def split_by_whitespace(sentence):
    words = []
    for space_separated_fragment in sentence.strip().split():
        words.extend(re.split(" ", space_separated_fragment))
    return [w for w in words if w]


def intstr_to_intlist(string):
    """Given a string e.g. '311 9 1334 635 6192 56 639', returns as a list of integers"""
    return [int(s) for s in string.split()]


def sentence_to_token_ids(sentence, word2id):
    """Turns an already-tokenized sentence string into word indices
    e.g. "i do n't know" -> [9, 32, 16, 96]
    Note any token that isn't in the word2id mapping gets mapped to the id for UNK
    """
    tokens = split_by_whitespace(sentence) # list of strings
    ids = [word2id.get(w, UNK_ID) for w in tokens]
    return tokens, ids


def padded(token_batch, batch_pad=0):
    """
    Inputs:
      token_batch: List (length batch size) of lists of ints.
      batch_pad: Int. Length to pad to. If 0, pad to maximum length sequence in token_batch.
    Returns:
      List (length batch_size) of padded of lists of ints.
        All are same length - batch_pad if batch_pad!=0, otherwise the maximum length in token_batch
    """
    maxlen = max(map(lambda x: len(x), token_batch)) if batch_pad == 0 else batch_pad
    return map(lambda token_list: token_list + [PAD_ID] * (maxlen - len(token_list)), token_batch)

def paddedBool(token_batch, batch_pad=0):
    maxlen = max(map(lambda x: len(x), token_batch)) if batch_pad == 0 else batch_pad
    return map(lambda token_list: token_list + [False] * (maxlen - len(token_list)), token_batch)

def padded2(token_batch, num_feats, batch_pad=0, islist=False):
    maxlen = max(map(lambda x: len(x), token_batch)) if batch_pad == 0 else batch_pad
    if islist:
        return map(lambda token_list: token_list + [num_feats*[PAD_ID,]] * (maxlen - len(token_list)) , token_batch)
    else:
        return map(lambda token_list: token_list + [num_feats*(PAD_ID,)] * (maxlen - len(token_list)) , token_batch)


def refill_batches(batches, word2id, context_file, qn_file, ans_file, batch_size, context_len, question_len, discard_long, word_len, mcids_dict):
    """
    Adds more batches into the "batches" list.

    Inputs:
      batches: list to add batches to
      word2id: dictionary mapping word (string) to word id (int)
      context_file, qn_file, ans_file: paths to {train/dev}.{context/question/answer} data files
      batch_size: int. how big to make the batches
      context_len, question_len: max length of context and question respectively
      discard_long: If True, discard any examples that are longer than context_len or question_len.
        If False, truncate those exmaples instead.
    """
    print "Refilling batches..."
    tic = time.time()
    examples = [] # list of (qn_ids, context_ids, ans_span, ans_tokens) triples
    context_line, qn_line, ans_line = context_file.readline(), qn_file.readline(), ans_file.readline() # read the next line from each


    pos2int = {"CC":0, "CD":1, "DT":2, "EX":3, "FW":4, "IN":5, "JJ":6, "JJR":7, "JJS":8, \
        "LS":9, "MD":10, "NN":11, "NNS":12, "NNP":13, "NNPS":14, "PDT":15, "POS":16, \
        "PRP":17, "PRP$":18, "RB":19, "RBR":20, "RBS":21, "RP":22, "SYM":23, "TO":24, \
        "UH":25, "VB":26, "VBD":27, "VBG":28, "VBN":29, "VBP":30, "VBZ":31, "WDT":32, \
        "WP":33, "WP$":34, "WRB":35}
    ner2int = {"O":0, "PERSON":1, "LOCATION":2, "ORGANIZATION":3, "GSP":4, "GPE":5, "FACILITY":6}
    pos_keys = pos2int.keys()
    ner_keys = ner2int.keys() 
    # lemmatizer = WordNetLemmatizer()
    a = 0.4

    char2id = {"a":2, "b":3, "c":4, "d":5, "e":6, "f":7, "g":8, \
        "h":9, "i":10, "j":11, "k":12, "l":13, "m":14, "n":15, "o":16, \
        "p":17, "q":18, "r":19, "s":20, "t":21, "u":22, "v":23, "w":24, \
        "x":25, "y":26, "z":27, "0":28, "1":29, "2":30, "3":31, "4":32, \
        "5":33, "6":34, "7":35, "8":36, "9":37, ".":38, ",":39, '"':40, \
        "?":41, "'":42}
    char_keys = char2id.keys()

    mcids_keys = mcids_dict.keys()

    while context_line and qn_line and ans_line: # while you haven't reached the end

        # Convert tokens to word ids
        context_tokens, context_ids = sentence_to_token_ids(context_line, word2id)
        qn_tokens, qn_ids = sentence_to_token_ids(qn_line, word2id)
        ans_span = intstr_to_intlist(ans_line)

        ########## GENERATE CHARACTER TOKENS #########################
        char_ids = [[char2id[char] if char in char_keys else UNK_ID for char in tok.lower()] for tok  in context_tokens]
        char_ids = [x[:word_len] for x in char_ids] # (N, <=word_len)
        char_ids = padded(char_ids, word_len) # (N, word_len)

        charQ_ids = [[char2id[char] if char in char_keys else UNK_ID for char in tok.lower()] for tok  in qn_tokens]
        charQ_ids = [x[:word_len] for x in charQ_ids] # (M, <=word_len)
        charQ_ids = padded(charQ_ids, word_len) # (M, word_len)
        ##############################################################

        ########## GET COMMONQ EMBEDDING INDICES AND MASK ############
        commonQ_mask        = [x in mcids_keys for x in qn_ids] # (M)
        commonQ_emb_indices = [mcids_dict.get(x,0) for x in qn_ids] # (M) - note the 0 index doesnt matter due to mask

        commonC_mask        = [x in mcids_keys for x in context_ids] # (N)
        commonC_emb_indices = [mcids_dict.get(x,0) for x in context_ids] # (N) - note the 0 index doesnt matter due to mask
        ##############################################################

        ########## GENERATE EXACT MATCH + POS/NER FEATURES ###########
        # calculate POS and NER tags (as strings)
        # pos_tree = pos_tag(context_tokens)
        # pos_tags = [p[1] for p in pos_tree]
        # chunk = ne_chunk(pos_tree)
        # ner_tags = [ne[2][2:] for ne in tree2conlltags(chunk)]

        # convert POS and NER tags to ints using dictionary
        # pos_ids = [pos2int[pos] if pos in pos_keys else -1 for pos in pos_tags]
        # ner_ids = [ner2int[ne]  if ne  in ner_keys else 0  for ne  in ner_tags]

        # compute lemmatized version of each context token                
        # lems = [str(lemmatizer.lemmatize(tok,get_wordnet_pos(pos))) if get_wordnet_pos(pos) else str(lemmatizer.lemmatize(tok)) for tok,pos in zip(context_tokens,pos_tags)]

        # compare each context word to query words for three different versions
        match_orig  = [int(sum([context_token==q     for q in qn_tokens])==1) for context_token     in context_tokens] # original form
        # match_lemma = [int(sum([context_token_lem==q for q in qn_tokens])==1) for context_token_lem in lems]    # lemma form

        # compute normalized term frequency
        fdist = FreqDist(context_tokens)
        max_count = float(max(fdist.values()))
        tf = [a + (1-a)*fdist[w]/max_count for w in context_tokens]

        # feats = zip(*(pos_ids, ner_ids, match_orig, match_lemma))  # (N,4)
        # feats = zip(*(pos_ids, match_orig, match_lemma))  # (N,3)
        # feats = zip(*(pos_ids, tf, match_orig, match_lemma))  # (N,4)
        feats = zip(*(tf, match_orig))  # (N,4)
        ##############################################################

        # read the next line from each file
        context_line, qn_line, ans_line = context_file.readline(), qn_file.readline(), ans_file.readline()

        # get ans_tokens from ans_span
        assert len(ans_span) == 2
        if ans_span[1] < ans_span[0]:
            print "Found an ill-formed gold span: start=%i end=%i" % (ans_span[0], ans_span[1])
            continue
        ans_tokens = context_tokens[ans_span[0] : ans_span[1]+1] # list of strings

        # discard or truncate too-long questions
        if len(qn_ids) > question_len:
            if discard_long:
                continue
            else: # truncate
                qn_ids = qn_ids[:question_len]
                commonQ_mask = commonQ_mask[:question_len]
                commonQ_emb_indices = commonQ_emb_indices[:question_len]
                charQ_ids = charQ_ids[:question_len]

        # discard or truncate too-long contexts
        if len(context_ids) > context_len:
            if discard_long:
                continue
            else: # truncate
                context_ids = context_ids[:context_len]
                feats = feats[:context_len]
                char_ids = char_ids[:context_len]
                commonC_mask = commonC_mask[:context_len]
                commonC_emb_indices = commonC_emb_indices[:context_len]

        # add to examples
        examples.append((context_ids, context_tokens, qn_ids, qn_tokens, ans_span, ans_tokens, feats, char_ids, commonQ_mask, commonQ_emb_indices, charQ_ids, commonC_mask, commonC_emb_indices))

        # stop refilling if you have 160 batches
        if len(examples) == batch_size * 160:
            break

    # Once you've either got 160 batches or you've reached end of file:

    # Sort by question length
    # Note: if you sort by context length, then you'll have batches which contain the same context many times (because each context appears several times, with different questions)
    examples = sorted(examples, key=lambda e: len(e[2]))

    # Make into batches and append to the list batches
    for batch_start in xrange(0, len(examples), batch_size):

        # Note: each of these is a list length batch_size of lists of ints (except on last iter when it might be less than batch_size)
        context_ids_batch, context_tokens_batch, qn_ids_batch, qn_tokens_batch, ans_span_batch, ans_tokens_batch, feats_batch, char_ids_batch, commonQ_mask_batch, commonQ_emb_indices_batch, charQ_ids_batch, commonC_mask_batch, commonC_emb_indices_batch = zip(*examples[batch_start:batch_start+batch_size])

        batches.append((context_ids_batch, context_tokens_batch, qn_ids_batch, qn_tokens_batch, ans_span_batch, ans_tokens_batch, feats_batch, char_ids_batch, commonQ_mask_batch, commonQ_emb_indices_batch, charQ_ids_batch, commonC_mask_batch, commonC_emb_indices_batch))

    # shuffle the batches
    random.shuffle(batches)

    toc = time.time()
    print "Refilling batches took %.2f seconds" % (toc-tic)
    return


def get_batch_generator(word2id, context_path, qn_path, ans_path, batch_size, context_len, question_len, discard_long, num_feats, word_len, mcids_dict):
    """
    This function returns a generator object that yields batches.
    The last batch in the dataset will be a partial batch.
    Read this to understand generators and the yield keyword in Python: https://stackoverflow.com/questions/231767/what-does-the-yield-keyword-do

    Inputs:
      word2id: dictionary mapping word (string) to word id (int)
      context_file, qn_file, ans_file: paths to {train/dev}.{context/question/answer} data files
      batch_size: int. how big to make the batches
      context_len, question_len: max length of context and question respectively
      discard_long: If True, discard any examples that are longer than context_len or question_len.
        If False, truncate those exmaples instead.
    """
    context_file, qn_file, ans_file = open(context_path), open(qn_path), open(ans_path)
    batches = []

    while True:
        if len(batches) == 0: # add more batches
            refill_batches(batches, word2id, context_file, qn_file, ans_file, batch_size, context_len, question_len, discard_long, word_len, mcids_dict)
        if len(batches) == 0:
            break

        # Get next batch. These are all lists length batch_size
        (context_ids, context_tokens, qn_ids, qn_tokens, ans_span, ans_tokens, feats, char_ids, commonQ_mask, commonQ_emb_indices, charQ_ids, commonC_mask, commonC_emb_indices) = batches.pop(0)

        # Pad context_ids and qn_ids
        qn_ids = padded(qn_ids, question_len) # pad questions to length question_len
        context_ids = padded(context_ids, context_len) # pad contexts to length context_len

        # Make qn_ids into a np array and create qn_mask
        qn_ids = np.array(qn_ids) # shape (question_len, batch_size)
        qn_mask = (qn_ids != PAD_ID).astype(np.int32) # shape (question_len, batch_size)

        # Make context_ids into a np array and create context_mask
        context_ids = np.array(context_ids) # shape (context_len, batch_size)
        context_mask = (context_ids != PAD_ID).astype(np.int32) # shape (context_len, batch_size)

        # Make ans_span into a np array
        ans_span = np.array(ans_span) # shape (batch_size, 2)

        # Make feats into an np array
        feats = np.array(padded2(feats, num_feats, context_len))

        # Pad character ids (first for word length, then for context length), then make into array
        char_ids = padded2(char_ids, word_len, context_len, islist=True)
        char_ids = np.array(char_ids)
        char_mask = (char_ids != PAD_ID).astype(np.int32)

        charQ_ids = padded2(charQ_ids, word_len, question_len, islist=True)
        charQ_ids = np.array(charQ_ids)
        charQ_mask = (charQ_ids != PAD_ID).astype(np.int32)

        # Pad commonQ_mask and commonQ_emb_indices / convert to np.array
        commonQ_mask = np.array(paddedBool(commonQ_mask, question_len))
        commonQ_emb_indices = np.array(padded(commonQ_emb_indices, question_len))

        commonC_mask = np.array(paddedBool(commonC_mask, context_len))
        commonC_emb_indices = np.array(padded(commonC_emb_indices, context_len))

        # Make into a Batch object
        batch = Batch(context_ids, context_mask, context_tokens, qn_ids, qn_mask, qn_tokens, ans_span, ans_tokens, feats, char_ids, char_mask, commonQ_mask, commonQ_emb_indices, charQ_ids, charQ_mask, commonC_mask, commonC_emb_indices)

        yield batch

    return