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

"""This file contains some basic model components"""

import tensorflow as tf
from tensorflow.python.ops.rnn_cell import DropoutWrapper
from tensorflow.python.ops import variable_scope as vs
from tensorflow.python.ops import rnn_cell


class RNNEncoder(object):
    """
    General-purpose module to encode a sequence using a RNN.
    It feeds the input through a RNN and returns all the hidden states.

    Note: In lecture 8, we talked about how you might use a RNN as an "encoder"
    to get a single, fixed size vector representation of a sequence
    (e.g. by taking element-wise max of hidden states).
    Here, we're using the RNN as an "encoder" but we're not taking max;
    we're just returning all the hidden states. The terminology "encoder"
    still applies because we're getting a different "encoding" of each
    position in the sequence, and we'll use the encodings downstream in the model.

    This code uses a bidirectional GRU, but you could experiment with other types of RNN.
    """

    def __init__(self, hidden_size, keep_prob, num_rnn_layers, scope):
        """
        Inputs:
          hidden_size: int. Hidden size of the RNN
          keep_prob: Tensor containing a single scalar that is the keep probability (for dropout)
        """
        self.hidden_size = hidden_size
        self.keep_prob = keep_prob
        self.scope = scope

        # ## Basic one layer RNN implementaiton
        # self.rnn_cell_fw = rnn_cell.GRUCell(self.hidden_size)
        # self.rnn_cell_fw = DropoutWrapper(self.rnn_cell_fw, input_keep_prob=self.keep_prob)
        # self.rnn_cell_bw = rnn_cell.GRUCell(self.hidden_size)
        # self.rnn_cell_bw = DropoutWrapper(self.rnn_cell_bw, input_keep_prob=self.keep_prob)

        ## Stacked layer RNN implementaiton
        self.num_rnn_layers = num_rnn_layers
        self.rnn_cells_fw = [DropoutWrapper(rnn_cell.LSTMCell(self.hidden_size), input_keep_prob=self.keep_prob) for _ in range(num_rnn_layers)]
        self.rnn_cells_bw = [DropoutWrapper(rnn_cell.LSTMCell(self.hidden_size), input_keep_prob=self.keep_prob) for _ in range(num_rnn_layers)]

    def build_graph(self, inputs, masks):
        """
        Inputs:
          inputs: Tensor shape (batch_size, seq_len, input_size)
          masks: Tensor shape (batch_size, seq_len).
            Has 1s where there is real input, 0s where there's padding.
            This is used to make sure tf.nn.bidirectional_dynamic_rnn doesn't iterate through masked steps.

        Returns:
          out: Tensor shape (batch_size, seq_len, hidden_size*2).
            This is all hidden states (fw and bw hidden states are concatenated).
        """
        with vs.variable_scope(self.scope):
            input_lens = tf.reduce_sum(masks, reduction_indices=1) # shape (batch_size)

            # ## Using basic one-layer bidirectional RNN
            # # Note: fw_out and bw_out are the hidden states for every timestep.
            # # Each is shape (batch_size, seq_len, hidden_size).
            # (fw_out, bw_out), _ = tf.nn.bidirectional_dynamic_rnn(self.rnn_cell_fw, self.rnn_cell_bw, inputs, input_lens, dtype=tf.float32)
            # # Concatenate the forward and backward hidden states
            # out = tf.concat([fw_out, bw_out], 2)

            ## Multi-layer bidirectional RNN
            out = inputs
            for n in range(self.num_rnn_layers):
                (fw_out, bw_out), _ = tf.nn.bidirectional_dynamic_rnn(self.rnn_cells_fw[n], self.rnn_cells_bw[n], out, input_lens, dtype=tf.float32, scope="bidirectional_rnn_" + str(n))
                out = tf.concat([fw_out, bw_out], axis=2)
                print('ADDED NEW LAYER ' + str(n))

            # Apply dropout
            out = tf.nn.dropout(out, self.keep_prob)

            return out

class SimpleSoftmaxLayer(object):
    """
    Module to take set of hidden states, (e.g. one for each context location),
    and return probability distribution over those states.
    """

    def __init__(self):
        pass

    def build_graph(self, inputs, masks):
        """
        Applies one linear downprojection layer, then softmax.

        Inputs:
          inputs: Tensor shape (batch_size, seq_len, hidden_size)
          masks: Tensor shape (batch_size, seq_len)
            Has 1s where there is real input, 0s where there's padding.

        Outputs:
          logits: Tensor shape (batch_size, seq_len)
            logits is the result of the downprojection layer, but it has -1e30
            (i.e. very large negative number) in the padded locations
          prob_dist: Tensor shape (batch_size, seq_len)
            The result of taking softmax over logits.
            This should have 0 in the padded locations, and the rest should sum to 1.
        """
        with vs.variable_scope("SimpleSoftmaxLayer"):

            # Linear downprojection layer
            logits = tf.contrib.layers.fully_connected(inputs, num_outputs=1, activation_fn=None) # shape (batch_size, seq_len, 1)
            logits = tf.squeeze(logits, axis=[2]) # shape (batch_size, seq_len)

            # Take softmax over sequence
            masked_logits, prob_dist = masked_softmax(logits, masks, 1)

            return masked_logits, prob_dist

class BasicAttn(object):
    """Module for basic attention.

    Note: in this module we use the terminology of "keys" and "values" (see lectures).
    In the terminology of "X attends to Y", "keys attend to values".

    In the baseline model, the keys are the context hidden states
    and the values are the question hidden states.

    We choose to use general terminology of keys and values in this module
    (rather than context and question) to avoid confusion if you reuse this
    module with other inputs.
    """

    def __init__(self, keep_prob, key_vec_size, value_vec_size):
        """
        Inputs:
          keep_prob: tensor containing a single scalar that is the keep probability (for dropout)
          key_vec_size: size of the key vectors. int
          value_vec_size: size of the value vectors. int
        """
        self.keep_prob = keep_prob
        self.key_vec_size = key_vec_size
        self.value_vec_size = value_vec_size

    def build_graph(self, values, values_mask, keys, hidden_size):
        """
        Keys attend to values.
        For each key, return an attention distribution and an attention output vector.

        Inputs:
          values: Tensor shape (batch_size, num_values, value_vec_size).
          values_mask: Tensor shape (batch_size, num_values).
            1s where there's real input, 0s where there's padding
          keys: Tensor shape (batch_size, num_keys, value_vec_size)

        Outputs:
          attn_dist: Tensor shape (batch_size, num_keys, num_values).
            For each key, the distribution should sum to 1,
            and should be 0 in the value locations that correspond to padding.
          output: Tensor shape (batch_size, num_keys, hidden_size).
            This is the attention output; the weighted sum of the values
            (using the attention distribution as weights).
        """
        with vs.variable_scope("BasicAttn"):

            # note: applying the dense connection below effectively makes value_vec_size = key_vec_size = hidden_size
            values = tf.layers.dense(values, hidden_size, activation=tf.nn.relu, reuse=tf.AUTO_REUSE, name="atnnDense", kernel_initializer=tf.contrib.layers.xavier_initializer())
            keys   = tf.layers.dense(keys,   hidden_size, activation=tf.nn.relu, reuse=tf.AUTO_REUSE, name="atnnDense")

            # Calculate attention distribution
            values_t = tf.transpose(values, perm=[0, 2, 1]) # (batch_size, value_vec_size, num_values)
            attn_logits = tf.matmul(keys, values_t) # shape (batch_size, num_keys, num_values)
            attn_logits_mask = tf.expand_dims(values_mask, 1) # shape (batch_size, 1, num_values)
            _, attn_dist = masked_softmax(attn_logits, attn_logits_mask, 2) # shape (batch_size, num_keys, num_values). take softmax over values

            # Use attention distribution to take weighted sum of values
            output = tf.matmul(attn_dist, values) # shape (batch_size, num_keys, value_vec_size)

            # Apply dropout
            output = tf.nn.dropout(output, self.keep_prob)

            return attn_dist, output

class BidirecAttn(object):
    """Module for bidirectional attention.
        Implementation described in original BiDAF paper (Seo et al)
    """

    def __init__(self, keep_prob, hidden_size):
        """
        Inputs:
          keep_prob: tensor containing a single scalar that is the keep probability (for dropout)
          hidden_size: size of hidden layer. int
        """
        self.keep_prob   = keep_prob
        self.hidden_size = hidden_size

    def build_graph(self, c, c_mask, q, q_mask):
        """
        Inputs:
          c:        context embeddings,  Tensor shape (batch_size, N, 2h).
          q:        question embeddings, Tensor shape (batch_size, M, 2h)
          c_mask:   context mask,  Tensor shape (batch_size, N).
          q_mask:   question mask, Tensor shape (batch_size, M).

        Outputs:.
          output: Tensor shape (batch_size, num_context, hidden_size*8).
            This is the concatentation of context hidden state, C2Q attention output, and Q2C attention output.
        """
        with vs.variable_scope("BidirecAttn"):

            N = tf.shape(c)[1] # num_context
            M = tf.shape(q)[1] # num_question

            ## Compute similarity matrix
            w_bd1 = tf.get_variable("w_bd1", shape=(self.hidden_size*2), initializer=tf.contrib.layers.xavier_initializer()) # (2h)
            w_bd2 = tf.get_variable("w_bd2", shape=(self.hidden_size*2), initializer=tf.contrib.layers.xavier_initializer()) # (2h)
            w_bd3 = tf.get_variable("w_bd3", shape=(self.hidden_size*2), initializer=tf.contrib.layers.xavier_initializer()) # (2h)
            
            w_bd1_aug = tf.expand_dims(w_bd1,1)             # (2h, 1)
            S1 = tf.tensordot(c, w_bd1_aug, axes=[[2],[0]]) # (b, N, 1) = (b,N,2h)x(2h,1); (can use add broadcasting later)

            w_bd2_aug = tf.expand_dims(w_bd2,0)             # (1, 2h)
            S2 = tf.tensordot(w_bd2_aug, q, axes=[[1],[2]]) # (1, b, M) = (1,2h)x(b,M,2h)
            S2 = tf.transpose(S2, perm=[1,0,2])             # (b, 1, M); (can use add broadcasting later)

            w_bd3_aug = tf.expand_dims(tf.expand_dims(w_bd3,0),0)  # (1, 1, 2h)
            w3_c = w_bd3_aug * c                                 # (b, N, 2h) with broadcasting
            S3 = tf.matmul(w3_c, tf.transpose(q,perm=[0,2,1]))   # (b, N, M)

            S = S1+S2+S3 # (b, N, M)

            ## Compute C2Q attention
            alpha,_ = masked_softmax(S, tf.expand_dims(q_mask,1), dim=2)  # take row-wise softmax of S; (b, N, M)
            a = tf.expand_dims(alpha,3) * tf.expand_dims(q,1) # (b, N, M, 2h) = (b,N,M,1)*(b,1,M,2H)
            a = tf.reduce_sum(a, axis=2) # (b,N,2h)
            a = tf.nn.dropout(a, self.keep_prob)

            ## Compute Q2C attention
            m = tf.reduce_max(S, axis=2)               # (b, N)
            beta,_ = masked_softmax(m, c_mask, dim=1)  # (b, N)
            beta   = tf.expand_dims(beta, axis=2)      # (b, N, 1)
            cprime = beta * c                          # (b, N, 2h) = (b,N,1)x(b,N,2h)
            cprime = tf.reduce_sum(cprime, axis=1)     # (b, 2h)
            cprime = tf.expand_dims(cprime, axis=1)    # (b, 1, 2h)
            cprime = tf.nn.dropout(cprime, self.keep_prob)

            # form final output
            output = tf.concat([c, a, c*a, c*cprime], axis=2) # (b, N, 8h)
            return output, alpha

class SelfAttn(object):
    """
        Module for self-attention layer. Implementation described in R-Net.
    """

    def __init__(self, keep_prob, hidden_size):
        """
        Inputs:
          keep_prob: tensor containing a single scalar that is the keep probability (for dropout)
          hidden_size: size of hidden layer. int
        """
        self.keep_prob   = keep_prob
        self.hidden_size = hidden_size

    def build_graph(self, c, c_mask):
        """
        Inputs:
          c:        context embeddings,  Tensor shape (batch_size, N, l).
          c_mask:   context mask,  Tensor shape (batch_size, N).

        Outputs:.
          output: Tensor shape ().
        """
        with vs.variable_scope("SelfAttn"):

            l  = c.get_shape().as_list()[2]
            v  = tf.get_variable("v",  shape=(self.hidden_size),    initializer=tf.contrib.layers.xavier_initializer()) # (h)
            W1 = tf.get_variable("W1", shape=(self.hidden_size, l), initializer=tf.contrib.layers.xavier_initializer()) # (h,l)
            W2 = tf.get_variable("W2", shape=(self.hidden_size, l), initializer=tf.contrib.layers.xavier_initializer()) # (h,l)
            
            Q1 = tf.tensordot(c, W1, axes=[[2],[1]]) # (b, N, h) = (b,N,l)x(h,l)
            Q2 = tf.tensordot(c, W2, axes=[[2],[1]]) # (b, N, h) = (b,N,l)x(h,l)

            Q1 = tf.expand_dims(Q1,1) # (b, 1, N, h) 
            Q2 = tf.expand_dims(Q2,2) # (b, N, 1, h) 

            Q = tf.tanh(Q1 + Q2) # (b, N, N, h)
            e = tf.tensordot(Q, v, axes=[[3],[0]]) # (b, N, N)
            alpha,_ = masked_softmax(e, tf.expand_dims(c_mask,1), dim=2)  # take row-wise softmax of e; (b, N, N)
            alpha = tf.expand_dims(alpha,3) # (b, M, N, 1) # note, M=N, denote M for clarity
            a = alpha*tf.expand_dims(c,1)   # (b, M, N, l) # note, M=N, denote M for clarity
            a = tf.reduce_sum(a,axis=2)  # (b, M, l)    # note, M=N, denote M for clarity

            rnn_input = tf.concat([c,a],axis=2) # (b,N,2*l)

            input_lens = tf.reduce_sum(c_mask, reduction_indices=1) # shape (batch_size)
            self.rnn_cell_fw = DropoutWrapper(rnn_cell.GRUCell(self.hidden_size), input_keep_prob=self.keep_prob)
            self.rnn_cell_bw = DropoutWrapper(rnn_cell.GRUCell(self.hidden_size), input_keep_prob=self.keep_prob)
            
            # Each is shape (batch_size, seq_len, hidden_size).
            (fw_out, bw_out), _ = tf.nn.bidirectional_dynamic_rnn(self.rnn_cell_fw, self.rnn_cell_bw, rnn_input, input_lens, dtype=tf.float32)
            out = tf.concat([fw_out, bw_out], 2) # (b,N,2*h)
            out = tf.nn.dropout(out, self.keep_prob)
            return out

def masked_softmax(logits, mask, dim):
    """
    Takes masked softmax over given dimension of logits.

    Inputs:
      logits: Numpy array. We want to take softmax over dimension dim.
      mask: Numpy array of same shape as logits.
        Has 1s where there's real data in logits, 0 where there's padding
      dim: int. dimension over which to take softmax

    Returns:
      masked_logits: Numpy array same shape as logits.
        This is the same as logits, but with 1e30 subtracted
        (i.e. very large negative number) in the padding locations.
      prob_dist: Numpy array same shape as logits.
        The result of taking softmax over masked_logits in given dimension.
        Should be 0 in padding locations.
        Should sum to 1 over given dimension.
    """
    exp_mask = (1 - tf.cast(mask, 'float')) * (-1e30) # -large where there's padding, 0 elsewhere
    masked_logits = tf.add(logits, exp_mask) # where there's padding, set logits to -large
    prob_dist = tf.nn.softmax(masked_logits, dim)
    return masked_logits, prob_dist
