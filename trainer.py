# Copyright 2014 Matthieu Courbariaux

# This file is part of deep-learning-storage.

# deep-learning-storage is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# deep-learning-storage is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with deep-learning-storage.  If not, see <http://www.gnu.org/licenses/>.

import gzip
import cPickle
import numpy as np
import os
import os.path
import sys
import theano 
import theano.tensor as T
import time

# TRAINING

class Trainer(object):
    
    def __init__(self,
            rng,
            train_set, valid_set, test_set,
            model,
            LR, LR_decay, LR_fin,
            batch_size, gpu_batches,
            n_epoch, monitor_step,
            shuffle_batches, shuffle_examples):
        
        print '    Training algorithm:'
        print '        Learning rate = %f' %(LR)
        print '        Learning rate decay = %f' %(LR_decay)
        print '        Final learning rate = %f' %(LR_fin)
        print '        Batch size = %i' %(batch_size)
        print '        gpu_batches = %i' %(gpu_batches)
        print '        Number of epochs = %i' %(n_epoch)
        print '        Monitor step = %i' %(monitor_step)
        print '        shuffle_batches = %i' %(shuffle_batches)
        print '        shuffle_examples = %i' %(shuffle_examples)

        # save the dataset
        self.rng = rng
        self.shuffle_batches = shuffle_batches
        self.shuffle_examples = shuffle_examples
        self.train_set = train_set
        self.valid_set = valid_set
        self.test_set = test_set
        
        # save the model
        self.model = model
        
        # save the parameters
        self.LR = LR
        self.LR_decay = LR_decay
        self.LR_fin = LR_fin
        self.batch_size = batch_size
        self.gpu_batches = gpu_batches
        self.n_epoch = n_epoch
        self.step = monitor_step
        self.format = format
        
        # put a part of the dataset on gpu
        self.shared_x = theano.shared(
            np.asarray(self.train_set.X[0:self.batch_size*self.gpu_batches], dtype=theano.config.floatX))
        self.shared_y = theano.shared(
            np.asarray(self.train_set.y[0:self.batch_size*self.gpu_batches], dtype=theano.config.floatX))
    
    def shuffle(self, set):
        
        # on the CPU for the moment.
        X = np.copy(set.X)
        y = np.copy(set.y)
                
        shuffled_index = range(set.X.shape[0])
        self.rng.shuffle(shuffled_index)
        
        for i in range(set.X.shape[0]):
            set.X[i] = X[shuffled_index[i]]
            set.y[i] = y[shuffled_index[i]]
    
    def init(self):
        
        self.epoch = 0
        self.best_epoch = self.epoch
        
        # test it on the training set
        self.train_ER = self.test_epoch(self.train_set, can_fit=1)
        # test it on the validation set
        self.validation_ER = self.test_epoch(self.valid_set)
        # test it on the test set
        self.test_ER = self.test_epoch(self.test_set)
        
        self.best_validation_ER = self.validation_ER
        self.best_test_ER = self.test_ER
    
    def update_LR(self):

        if self.LR > self.LR_fin:
            self.LR *= self.LR_decay
    
    def update(self):
        
        # start by shuffling train set
        if self.shuffle_examples == True:
            self.shuffle(self.train_set)
        
        self.epoch += self.step
        
        for k in range(self.step):
            # train the model on all training examples
            self.train_epoch(self.train_set)
            
            # update LR as well during the first phase
            self.update_LR()
        
        # test it on the training set
        self.train_ER = self.test_epoch(self.train_set, can_fit=1)
        
        # test it on the validation set
        self.validation_ER = self.test_epoch(self.valid_set)
        
        # test it on the test set
        self.test_ER = self.test_epoch(self.test_set) 
        

        
        # save the best parameters
        if self.validation_ER < self.best_validation_ER:
            self.best_validation_ER = self.validation_ER
            self.best_test_ER = self.test_ER
            self.best_epoch = self.epoch
    
    def load_shared_dataset(self, set, start,size):
        
        self.shared_x.set_value(
            set.X[self.batch_size*start:self.batch_size*(size+start)])
        self.shared_y.set_value(
            set.y[self.batch_size*start:self.batch_size*(size+start)])
    
    def train_epoch(self, set):
        
        # number of batch in the dataset
        n_batches = np.int(np.floor(set.X.shape[0]/self.batch_size))
        # number of group of batches (in the memory of the GPU)
        n_gpu_batches = np.int(np.floor(n_batches/self.gpu_batches))
        
        # number of batches in the last group
        if self.gpu_batches<=n_batches:
            n_remaining_batches = n_batches%self.gpu_batches
        else:
            n_remaining_batches = n_batches
        
        # batch counter for the range update frequency
        k = 0
        
        shuffled_range_i = range(n_gpu_batches)
        
        if self.shuffle_batches==True:
            self.rng.shuffle(shuffled_range_i)
        
        for i in shuffled_range_i:
        
            self.load_shared_dataset(set,
                start=i*self.gpu_batches,
                size=self.gpu_batches)
            
            shuffled_range_j = range(self.gpu_batches)
            
            if self.shuffle_batches==True:
                self.rng.shuffle(shuffled_range_j)
            
            for j in shuffled_range_j:  

                self.train_batch(j, self.LR)
        
        # load the last incomplete gpu batch of batches
        if n_remaining_batches > 0:
        
            self.load_shared_dataset(set,
                    start=n_gpu_batches*self.gpu_batches,
                    size=n_remaining_batches)
            
            shuffled_range_j = range(n_remaining_batches)
            if self.shuffle_batches==True:
                self.rng.shuffle(shuffled_range_j)
            
            for j in shuffled_range_j: 

                self.train_batch(j, self.LR)
    
    def test_epoch(self, set, can_fit=0):
        
        n_batches = 1
        n_gpu_batches = 1

        self.load_shared_dataset(set,start=0,size=set.X.shape[0])
        error_rate = np.float(self.test_batch(can_fit))
        error_rate /= set.X.shape[0]
        error_rate *= 100.
        
        return error_rate
    
    def monitor(self):
    
        print '    epoch %i:' %(self.epoch)
        print '        learning rate %f' %(self.LR)
        print '        train error rate %f%%' %(self.train_ER)
        print '        validation error rate %f%%' %(self.validation_ER)
        print '        test error rate %f%%' %(self.test_ER)
        print '        epoch associated to best validation error %i' %(self.best_epoch)
        print '        best validation error rate %f%%' %(self.best_validation_ER)
        print '        test error rate associated to best validation error %f%%' %(self.best_test_ER)
        self.model.monitor()
    
    def train(self):        
        
        self.init()
        self.monitor()
        
        while (self.epoch < self.n_epoch):
            
            self.update()   
            self.monitor()
    
    def build(self):
        
        # input and output variables
        x = T.matrix('x')
        y = T.matrix('y')
        index = T.lscalar() 
        can_fit = T.lscalar() 
        LR = T.scalar('LR', dtype=theano.config.floatX)

        # before the build, you work with symbolic variables
        # after the build, you work with numeric variables
        
        self.train_batch = theano.function(inputs=[index,LR], updates=self.model.parameters_updates(x,y,LR),givens={ 
                x: self.shared_x[index * self.batch_size:(index + 1) * self.batch_size], 
                y: self.shared_y[index * self.batch_size:(index + 1) * self.batch_size]},
                name = "train_batch", on_unused_input='warn')
        
        self.test_batch = theano.function(inputs = [can_fit], outputs=self.model.errors(x,y, can_fit),
            updates=self.model.BN_updates(),
            givens={
                x: self.shared_x,
                y: self.shared_y},
                name = "test_batch")
