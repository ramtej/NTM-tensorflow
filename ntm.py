from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
import tensorflow as tf
from tensorflow.python.ops import array_ops
from tensorflow.python.ops.seq2seq import sequence_loss

import ntm_cell

import os
from ops import binary_cross_entropy_with_logits
from utils import progress

class NTM(object):
    def __init__(self, cell, sess,
                 min_length, max_length,
                 min_grad=-100, max_grad=+100, 
                 lr=1e-4, momentum=0.9, decay=0.95,
                 scope="NTM", forward_only=False):
        """Create a neural turing machine specified by NTMCell "cell".

        Args:
            cell: An instantce of NTMCell.
            sess: A TensorFlow session.
            min_length: Minimum length of input sequence.
            max_length: Maximum length of input sequence.
            min_grad: (optional) Minimum gradient for gradient clipping [-10].
            max_grad: (optional) Maximum gradient for gradient clipping [+10].
            lr: (optional) Learning rate [1e-4].
            momentum: (optional) Momentum of RMSProp [0.9].
            decay: (optional) Decay rate of RMSProp [0.95].
        """
        if not isinstance(cell, ntm_cell.NTMCell):
            raise TypeError("cell must be an instance of NTMCell")

        self.cell = cell
        self.sess = sess
        self.scope = scope

        self.lr = lr
        self.momentum = momentum
        self.decay = decay

        self.min_grad = min_grad
        self.max_grad = max_grad
        self.min_length = min_length
        self.max_length = max_length

        self.inputs = []
        self.outputs = {}
        self.prev_states = {}
        self.true_outputs = []
        self.start_symbol = tf.placeholder(tf.float32, [self.cell.input_dim],
                                           name='start_symbol')
        self.end_symbol = tf.placeholder(tf.float32, [self.cell.input_dim],
                                         name='end_symbol')

        self.losses = {}
        self.optims = {}
        self.grads = {}

        self.saver = None
        self.params = None

        self.global_step = tf.Variable(0, trainable=False)
        self.opt = tf.train.RMSPropOptimizer(self.lr,
                                             decay=self.decay,
                                             momentum=self.momentum)
        self.build_model(forward_only)

    def build_model(self, forward_only):
        print(" [*] Building a NTM model")

        with tf.variable_scope(self.scope):
            # present start symbol
            _, prev_state = self.cell(self.start_symbol, state=None)
            zeros = np.zeros(self.cell.input_dim, dtype=np.float32)

            tf.get_variable_scope().reuse_variables()
            for seq_length in xrange(1, self.max_length + 1):
                progress(seq_length/float(self.max_length))

                input_ = tf.placeholder(tf.float32, [self.cell.input_dim],
                                        name='input_%s' % seq_length)
                true_output = tf.placeholder(tf.float32, [self.cell.output_dim],
                                             name='true_output_%s' % seq_length)

                self.inputs.append(input_)
                self.true_outputs.append(true_output)

                # present inputs
                _, prev_state = self.cell(input_, prev_state)

                # present end symbol
                _, state = self.cell(self.end_symbol, prev_state)

                self.prev_states[seq_length] = state

                if not forward_only:
                    # present targets
                    outputs = []
                    for _ in xrange(seq_length):
                        output, state = self.cell(zeros, state)
                        outputs.append(output)

                    self.outputs[seq_length] = outputs

            if not forward_only:
                for seq_length in xrange(self.min_length, self.max_length + 1):
                    print(" [*] Building a loss model for seq_length %s" % seq_length)

                    loss = sequence_loss(logits=self.outputs[seq_length],
                                        targets=self.true_outputs[0:seq_length],
                                        weights=[1] * seq_length,
                                        num_decoder_symbols=-1, # trash
                                        average_across_timesteps=False,
                                        average_across_batch=False,
                                        softmax_loss_function=\
                                            binary_cross_entropy_with_logits)

                    self.losses[seq_length] = loss 

                    if not self.params:
                        self.params = tf.trainable_variables()

                    #grads, norm = tf.clip_by_global_norm(tf.gradients(loss, self.params), 5)

                    grads = []
                    for grad in tf.gradients(loss, self.params):
                        if grad:
                            grads.append(tf.clip_by_value(grad, self.min_grad, self.max_grad))
                        else:
                            grads.append(grad)

                    self.grads[seq_length] = grads
                    self.optims[seq_length] = self.opt.apply_gradients(zip(grads, self.params),
                                                                        global_step=self.global_step)

        self.saver = tf.train.Saver()

        print(" [*] Build a NTM model finished")

    def get_outputs(self, seq_length):
        if not self.outputs.has_key(seq_length):
            with tf.variable_scope(self.scope):
                tf.get_variable_scope().reuse_variables()

                zeros = np.zeros(self.cell.input_dim, dtype=np.float32)
                state = self.prev_states[seq_length]

                outputs = []
                for _ in xrange(seq_length):
                    output, state = self.cell(zeros, state)
                    outputs.append(output)

                self.outputs[seq_length] = outputs
        return self.outputs[seq_length]

    def get_loss(self, seq_length):
        if not self.outputs.has_key(seq_length):
            self.get_outputs(seq_length)

        if not self.losses.has_key(seq_length):
            loss = sequence_loss(logits=self.outputs[seq_length],
                                targets=self.true_outputs[0:seq_length],
                                weights=[1] * seq_length,
                                num_decoder_symbols=-1, # trash
                                average_across_timesteps=False,
                                average_across_batch=False,
                                softmax_loss_function=\
                                    binary_cross_entropy_with_logits)

            self.losses[seq_length] = loss 
        return self.losses[seq_length]

    @property
    def loss(self):
        return self.losses[self.cell.depth]

    @property
    def optim(self):
        return self.optims[self.cell.depth]

    def load(self, checkpoint_dir):
        print(" [*] Reading checkpoints...")
        ckpt = tf.train.get_checkpoint_state(checkpoint_dir)
        if ckpt and ckpt.model_checkpoint_path:
            ckpt_name = os.path.basename(ckpt.model_checkpoint_path)
            self.saver.restore(self.sess, os.path.join(checkpoint_dir, ckpt_name))
        else:
            raise Exception(" [!] Trest mode but no checkpoint found")
