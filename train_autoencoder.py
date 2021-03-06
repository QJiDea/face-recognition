#!/usr/bin/env python3
# coding: utf-8

'''Train a Stack AutoEncoder model.
'''

__author__ = 'IriKa'

import tensorflow as tf
import numpy as np
from autoencoder import stack_autoencoder as sae
from preprocessing import preprocessing_for_image as preprocess
from casia_webface import casia_webface as webface
from tools import variable_on_cpu

PI = 3.1415

class trainer:
    '''Stack AutoEncoder net train class.
    '''

    def __init__(self):
        self.faces = webface()

        # Report rate
        self.train_report_rate = 100
        self.test_report_rate = 500
        self.layer_report_rate = 500
        self.save_rate = 200

        # Hyper parameters
        self.ae_hidden_outputs = [16, 32, 32, 64]
        self.ae_hidden_layer_num = len(self.ae_hidden_outputs)
        self.pre_out_size = [128, 128]
        self.epochs = 10
        self.epoch_counter = self.faces.get_reshuffle_counter()
        self.batch_sizes = [64, 64, 32, 16]
        self.angles_max_delta = 15. / 180. * PI
        self.max_gradient = 1
        self.lr_init = 1e-1

        self.logs_dir = 'data/logs'
        self.model_fname = 'data/logs/autoencoder_model.ckpt'

        # PlaceHolder
        self.in_data_ph = tf.placeholder(dtype=tf.uint8, shape=[None, None, None, 3], name='input_data')
        self.train_ph = tf.placeholder(dtype=tf.bool, shape=[], name='is_train')
        #self.keep_prob = tf.placeholder(tf.float32, [], name='keep_prob')

        with tf.device('/device:GPU:0'):
            self.__model()
            with tf.variable_scope('train') as scope:
                self.train_ops = []
                for i in range(1, self.ae_hidden_layer_num+1):
                    global_step = variable_on_cpu('global_step_%d' % i, None, 0.0, trainable=False)
                    total_step = self.epochs * self.faces.size // self.batch_sizes[i-1]
                    print('The total_step for %d-th layer: %8s' % (i, total_step))
                    lr = tf.train.exponential_decay(
                                    self.lr_init,
                                    global_step,
                                    total_step,
                                    1e-3,
                                    name='auto_lr_%d' % i)
                    tf.summary.scalar('lr_%d' % i, lr, collections=['train'])
                    opt = tf.train.AdamOptimizer(learning_rate=lr)
                    vars_for_layer = self.autoencoder.get_variable_for_layer(i)
                    gradients = tf.gradients(self.loss, vars_for_layer)
                    clipped_gradients, _ = tf.clip_by_global_norm(gradients, self.max_gradient)
                    self.train_ops.append(opt.apply_gradients(
                                            zip(clipped_gradients,
                                                vars_for_layer),
                                            global_step=global_step,
                                            name='train_op_%d' % i))

        self.vars = tf.global_variables() + tf.local_variables()
        self.init_op = tf.initializers.variables(self.vars)

        # PlaceHolder
        self.rotate_angles_ph = self.prepro.get_placeholder()
        self.layer_train_ph = self.autoencoder.get_ph()

        self.config = tf.ConfigProto(allow_soft_placement=True)
        self.sess = tf.Session(config=self.config)

        # For summary, and you can visualize it with TensorBoard.
        self.saver = tf.train.Saver()
        self.summary_writer = tf.summary.FileWriter(self.logs_dir, graph=self.sess.graph)
        tf.summary.image('input_data', self.in_data_ph, collections=['layers_out'])
        for i in range(1, self.ae_hidden_layer_num+1):
            tf.summary.image('encode_%d' % i, self.autoencoder.get_encoded(i)[...,-1:], collections=['layers_out_%d' % i])

        self.train_merged = tf.summary.merge_all(key='train', name='train')
        self.layers_out_mergeds = []
        for i in range(1, self.ae_hidden_layer_num+1):
            self.layers_out_mergeds.append(tf.summary.merge_all(key='layers_out_%d' % i, name='layers_out_%d' % i))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        self.faces.close()
        self.sess.close()

    def reset_for_train(self):
        self.epoch_counter = self.faces.get_reshuffle_counter()

    def feed(self, is_train, train_layer):
        if self.faces.get_reshuffle_counter() - self.epoch_counter >= self.epochs:
            return None
        feed_dict = {}
        # FIXME: Please use asynchronous instead.
        batch, _ = self.faces.next_batch(batch_size=self.batch_sizes[train_layer-1])
        feed_dict[self.in_data_ph] = batch
        feed_dict[self.train_ph] = is_train
        feed_dict[self.rotate_angles_ph] = np.random.uniform(
                low=-self.angles_max_delta,
                high=self.angles_max_delta,
                size=self.batch_sizes[train_layer-1])
        feed_dict[self.layer_train_ph] = train_layer
        return feed_dict

    def __model(self):
        self.prepro = preprocess(self.in_data_ph, self.train_ph, out_size=self.pre_out_size, normalization=False)
        self.preprocessed = self.prepro.get_output()
        self.autoencoder = sae(self.preprocessed, self.ae_hidden_layer_num, self.ae_hidden_outputs, self.train_ph, need_norm=True)
        self.decoded = self.autoencoder.model()
        self.loss, self.l2_distance = self.autoencoder.loss(get_l2_distance=True)

        # For summary, and you can visualize it with TensorBoard.
        tf.summary.scalar('loss', self.loss, collections=['train'])
        tf.summary.scalar('similarity', self.l2_distance, collections=['train'])

    def train_a_step(self, layer_idx, global_step=0):
        feed_dict = self.feed(True, layer_idx)
        if feed_dict is None:
            # Complete the train.
            # May have the next round of training for the next layer of Stack AutoEncoder.
            self.reset_for_train()
            print('The %d-th layer of the Stack AutoEncoder has been trained.' % layer_idx)
            return None

        ops = [self.train_ops[layer_idx-1], self.loss, self.l2_distance]

        summary = False
        summary_for_layer = False
        if global_step % self.train_report_rate == 0:
            ops.append(self.train_merged)
            summary = True
        if global_step % self.layer_report_rate == 0:
            ops.append(self.layers_out_mergeds[layer_idx-1])
            summary_for_layer = True

        r = self.sess.run(ops, feed_dict=feed_dict)
        del r[0]
        if summary_for_layer:
            self.summary_writer.add_summary(r[-1], global_step=global_step)
            del r[-1]
        if summary:
            self.summary_writer.add_summary(r[-1], global_step=global_step)
            del r[-1]

        return r

    def train(self, restore=True):
        if restore:
            self.saver.restore(self.sess, self.model_fname)
        else:
            self.sess.run(self.init_op)

        step = 0
        try:
            train_layer = 1
            while True:
                if train_layer > self.ae_hidden_layer_num:
                    print('The whole Stack AutoEncoder has been trained completed.')
                    break
                r = self.train_a_step(train_layer, global_step=step)
                if r is None:
                    train_layer += 1
                    continue

                if step % 10 == 0:
                    train_loss, train_l2_distance = r
                    str_format = 'The step %8s-th: loss = %6.4s, l2_distance = %6.4s'
                    print(str_format % (step, train_loss, train_l2_distance))

                if train_layer == self.ae_hidden_layer_num and step % self.save_rate == 0:
                    self.saver.save(self.sess, self.model_fname, global_step=step)

                step += 1
        finally:
            self.saver.save(self.sess, self.model_fname, global_step=step)

def main():
    with trainer() as t:
        t.train(restore=False)

if __name__ == '__main__':
    main()

