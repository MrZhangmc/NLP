import tensorflow as tf
import numpy as np
import time
import datetime
import os
from text_cnn import TextCNN
import preprocessing
from tensorflow.contrib import learn
import draw

#Percentage of the training data to use for validation
dev_sample_percentage = .1
positive_data_file = './data/positive.txt'
negative_data_file = './data/negative.txt'

#Dimensionality of character embedding (default: 128)
embedding_dim = 128
#Comma-separated filter sizes (default: '3,4,5')
filter_sizes = [3,4,5]
#Number of filters per filter size (default: 128)
num_filters = 128
dropout_keep_prob = 0.5
l2_reg_lambda = 0.0

batch_size = 32
num_epochs = 5
#Evaluate model on dev set after this many steps (default: 100)
evaluate_every = 100
#Save model after this many steps (default: 100)
checkpoint_every = 100
#Number of checkpoints to store (default: 5)
num_checkpoints = 5

allow_soft_placement = True
log_device_placement = False

#Data Preparation
print('Loading data...')
x_text, y = preprocessing.load_data(positive_data_file, negative_data_file)
# Build vocabulary
#计算每行数据词数的最大长度
max_document_length = max([len(x.split(" ")) for x in x_text])
#print(max_document_length)
#进行词表填充，同时将词转化为索引序号
vocab = learn.preprocessing.VocabularyProcessor(max_document_length)
x = np.array(list(vocab.fit_transform(x_text)))

#Random shuffle data
np.random.seed(10)
# np.arange生成随机序列
shuffle_indices = np.random.permutation(np.arange(len(y)))
x_shuffle = x[shuffle_indices]
y_shuffle = y[shuffle_indices]

# Split train/test set
dev_sample_index = -1 * int(dev_sample_percentage * float(len(y)))
#print(dev_sample_index)
x_train, x_test = x_shuffle[:dev_sample_index], x_shuffle[dev_sample_index:]
y_train, y_test = y_shuffle[:dev_sample_index], y_shuffle[dev_sample_index:]
#print("Vocabulary Size: {:d}".format(len(vocab.vocabulary_)))
#print("Train/Dev split: {:d}/{:d}".format(len(y_train), len(y_test)))

#Training
#===============================
with tf.Graph().as_default():
    session_conf = tf.ConfigProto(
        allow_soft_placement = allow_soft_placement,
        log_device_placement = log_device_placement
    )
    sess = tf.Session(config=session_conf)
    with sess.as_default():
        cnn = TextCNN(
            sequence_size = x_train.shape[1],
            num_classes = y_train.shape[1],
            vocab_size = len(vocab.vocabulary_),
            embedding_size = embedding_dim,
            filter_sizes = filter_sizes,
            num_filters = num_filters,
            l2_reg_lambda = l2_reg_lambda
        )
        #Define training procedure
        global_step = tf.Variable(0, name="global_step", trainable=False)
        #print(global_step)
        optimizer = tf.train.AdamOptimizer(1e-3)
        grads_and_vars = optimizer.compute_gradients(cnn.loss)
        train_op = optimizer.apply_gradients(grads_and_vars, global_step=global_step)

        #记录梯度变化和稀疏度(可选)
        grad_summaries = []
        for g, v in grads_and_vars:
            if g is not None:
                grad_hist_summary = tf.summary.histogram("{}/grad/hist".format(v.name), g)
                sparsity_summary = tf.summary.scalar("{}/grad/sparsity".format(v.name), tf.nn.zero_fraction(g))
                grad_summaries.append(grad_hist_summary)
                grad_summaries.append(sparsity_summary)
        grad_summaries_merged = tf.summary.merge(grad_summaries)

        #定义模型保存的目录
        timestamp = str(int(time.time()))
        out_dir = os.path.abspath(os.path.join(os.path.curdir, "runs", timestamp))
        print('Writting to {}\n'.format(out_dir))

        #保存损失函数和准确率的参数
        loss_summary = tf.summary.scalar("loss", cnn.loss)
        acc_summary = tf.summary.scalar("accuracy", cnn.accuracy)

        #store training datas
        train_summary_op = tf.summary.merge([grad_summaries_merged, loss_summary, acc_summary])
        train_summary_dir = os.path.join(out_dir, "summaries", "train")
        train_summary_writer = tf.summary.FileWriter(train_summary_dir, sess.graph)

        #store test datas
        test_summary_op = tf.summary.merge([loss_summary, acc_summary])
        test_summary_dir = os.path.join(out_dir, "summaries", "test")
        test_summary_writer = tf.summary.FileWriter(test_summary_dir, sess.graph)

        #checkpoint dir 由于tf默认这个文件夹是存在的，所以需要新建
        checkpoint_dir = os.path.abspath(os.path.join(out_dir, "checkpoints"))
        checkpoint_prefix = os.path.join(checkpoint_dir, "models")
        if not os.path.exists(checkpoint_dir):
            os.mkdir(checkpoint_dir)
        saver = tf.train.Saver(tf.global_variables(), max_to_keep=num_checkpoints)
        vocab.save(os.path.join(out_dir, "vocab"))

        #初始化所有变量
        sess.run(tf.global_variables_initializer())


        # training step
        def train_step(x_batch, y_batch):
            feed_dict = {
                cnn.input_x:x_batch,
                cnn.input_y:y_batch,
                cnn.dropout_keep_prob:dropout_keep_prob
            }
            _, step, summaries, loss, accuracy = sess.run([train_op, global_step, train_summary_op, cnn.loss, cnn.accuracy], feed_dict)
            time_str = datetime.datetime.now().isoformat()
            print("{}: step {}, loss {:g}, acc {:g}".format(time_str, step, loss, accuracy))
            train_summary_writer.add_summary(summaries, step)
            return loss, accuracy

        #testing step
        def test_step(x_batch, y_batch, writer=None):
            feed_dict = {
                cnn.input_x:x_batch,
                cnn.input_y:y_batch,
                cnn.dropout_keep_prob:1.0
            }
            step, summaries, loss, accuracy = sess.run([global_step, test_summary_op, cnn.loss, cnn.accuracy], feed_dict)
            time_str = datetime.datetime.now().isoformat()
            print("{}: step {}, loss {:g}, acc {:g}".format(time_str, step, loss, accuracy))
            if writer:
                writer.add_summary(summaries, step)
            return loss, accuracy

        batches = preprocessing.batch_iter(list(zip(x_train, y_train)), batch_size, num_epochs)

        #存储训练集和样本集的loss和accuracy，以供之后的画图
        train_loss_all = []
        train_accuracy_all = []
        test_loss_all = []
        test_accuracy_all = []
        #进行训练 training loop for every step
        for batch in batches:
            x_batch, y_batch = zip(*batch)
            loss_train, accuracy_train = train_step(x_batch, y_batch)
            train_loss_all.append(loss_train)
            train_accuracy_all.append(accuracy_train)
            current_step = tf.train.global_step(sess, global_step)  #将Session和global_step
            #每evaluateevery次进行一次测试
            if current_step % evaluate_every == 0:
                print('\nTesting:')
                loss_test, accuracy_test = test_step(x_test, y_test, writer=test_summary_writer)
                test_loss_all.append(loss_test)
                test_accuracy_all.append(accuracy_test)
                print("")
            #每个checkpoint进行一次模型的保存
            if current_step % checkpoint_every == 0:
                path = saver.save(sess, './', global_step=current_step)
                print("Saved model checkpoint to {}\n".format(path))
        #draw picture for loss and accuracy of test and train
        draw.draw_picture('train', train_accuracy_all, train_loss_all)
        draw.draw_picture('test', test_accuracy_all, test_loss_all)

        print('modelling finished!')



