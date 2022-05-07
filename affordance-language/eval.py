import os
import csv
import json
import random
import argparse
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.models as models
from PIL import Image
from torchvision import transforms
import rnn

'''
Evaluate the performance of the trained model under different settings:
+ 'eval': held-out object set     RET_ACC Top1: 0.5666666666666667 Top2: 0.7416666666666667
+ 'YCB': YCB object set
+ 'robot': objects in the robot's environment, captured by the robot's camera
'''

parser = argparse.ArgumentParser()
parser.add_argument('--model', type=str, default='models/trained-model.pt',
                    help='saved checkpoint')
parser.add_argument('--input_test', type=str, default='data/clip-corpus-test.csv',
                    help='test data')
#below file is generated by ycb.py
parser.add_argument('--input_embedding', type=str, default='data/ycb-object-embedding.csv',
                    help='ycb image embeddings')
parser.add_argument('--ycb_vo', type=str, default='data/ycb-verb-object.csv',
                    help='ycb verb-object pairs for testing')
parser.add_argument('--image_dir', type=str, default='robot',
                    help='directory for robot images')
parser.add_argument('--command', type=str, default='e.g. An object to contain',
                    help='natural language command for robot')
parser.add_argument('--num_layers', type=int, default=1,
                    help='number of layers of model')
parser.add_argument('--rnn_input', type=int, default=128, help='')
parser.add_argument('--hidden_dim', type=int, default=64, help='')
parser.add_argument('--rnn_output', type=int, default=2048, help='')
parser.add_argument('--dropout', type=float, default=0.0, help='')
parser.add_argument('--ret_num', type=int, default=5, help='')
parser.add_argument('--mode', type=str, default='eval', help='3 possible evaluation modes: YCB, robot, eval')
parser.add_argument('--DEBUG', type=bool, default=True, help='')
parser.add_argument('--verb_only', type=bool, default=True, help='')
opt = parser.parse_args()

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
cuda = True if torch.cuda.is_available() else False
Tensor = torch.cuda.LongTensor if cuda else torch.LongTensor


#load resnet model
resnet = models.resnet101(pretrained=True)
#access average pooling layer in network
model_avgpool = nn.Sequential(*list(resnet.children())[:-1])
model_avgpool.eval()
preprocess = transforms.Compose([
    transforms.Resize(256),
    transforms.CenterCrop(224),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


#Load word vocab (generated in run.py)
word2id = None
with open("dict/word2id.json") as f:
    for line in f:
        word2id = json.loads(line)
id2word = None
with open("dict/id2word.json") as f:
    for line in f:
        id2word = json.loads(line)


#Load trained model
model = nn.Sequential(
nn.Embedding(len(word2id), opt.rnn_input),
rnn.RNNModel(opt.rnn_input, opt.rnn_output, opt.hidden_dim, opt.num_layers,
             opt.dropout, device)).to(device)
model.load_state_dict(torch.load(opt.model))


# Generate natural language command from templates, given verb-object pair
def gen_from_template(verb, obj):
    pre_obj = ['Give me the ', 'Hand me the ', 'Pass me the ', 'Fetch the ',
           'Get the ', 'Bring the ', 'Bring me the ',
           'I need the ', 'I want the ',
           'I need a ', 'I want a ']
    pre_verb = ['An item that can ', 'An object that can ',
           'Give me something that can ', 'Give me an item that can ',
           'Hand me something with which I can ',
           'Give me something with which I can ',
           'Hand me something to ', 'Give me something to ',
           'I want something to ', 'I need something to ']
    if opt.verb_only:
        template = random.choice(pre_verb)
        sentence = template + verb
    else:
        template = random.choice(pre_obj)
        sentence = template + obj + ' to ' + verb
    return sentence


# Map each word in the natural language command to its ID in the vocab
def process_command(command, word2id, id2word):
    sentence = []
    s = command.lower().split()
    for word in s:
        if word in word2id:
            sentence.append(word2id[word])
        else:
            sentence.append(word2id['UNK'])
    return sentence


# Generate retrieval tasks (from verb-object pairs) to test the model
def gen_ret(vo_dict, objects, aff_dict, word2id, id2word, exclude):
    ret_set = []
    for verb in vo_dict:
        for obj in vo_dict[verb]:
            if obj not in exclude:
                #generate language command from the verb-object pair
                sentence = gen_from_template(verb, obj)
                sentence = process_command(sentence, word2id, id2word)
                l = [sentence]
                ret_objs = [[obj]+random.choice(aff_dict[obj])]
                all_o = [obj]
                while len(ret_objs) < opt.ret_num:
                    o = random.choice(objects)
                    #only sample objects that cannot be paired with the current verb
                    #and ensure that the retrieval set has all unique objects
                    #(objects from different classes)
                    if (o not in vo_dict[verb]) and (o not in all_o) and (o not in exclude):
                        ret_objs.append([o]+random.choice(aff_dict[o]))
                        all_o.append(o)
                l.append(ret_objs)
                ret_set.append(l)
    return ret_set


# Generate retrieval tasks (from test examples) to test the model
def genRet(test, vo_dict):
    ret_set = []
    for verb, obj, sentence, affordances, img in test:
        l = [sentence]
        #the object included in the current test example
        #is the first candidate object for this retrieval task
        ret_objs = [[obj, affordances, img]]
        all_o = [obj]
        #each retrieval task includes ret_num (5) candidate objects
        #for the model to select from
        while len(ret_objs) < opt.ret_num:
            sample = random.choice(test)
            #only sample objects that cannot be paired with the current verb
            #and make sure that all objects in the retrieval set are unique
            #(from different object classes)
            if (sample[1] not in vo_dict[verb]) and (sample[1] not in all_o):
                ret_objs.append([sample[1], sample[3], sample[4]])
                all_o.append(sample[1])
        l.append(ret_objs)
        ret_set.append(l)
    return ret_set


# Test the model on retrieval tasks (selecting the correct object from a set of 5)
def ret(model, ret_set, id2word):
    model.eval()
    correct, correct2 = 0.0, 0.0
    with torch.no_grad():
        for sentence, ret_objs in ret_set:
            s = ''
            for i in sentence:
                s += id2word[str(i)] + ' '
            sentence = Tensor(sentence).unsqueeze(0)
            sims = []
            output = model(sentence)
            for obj_name, obj, img in ret_objs:
                obj = np.fromstring(obj[1:-1], dtype=float, sep=',')
                affordances = torch.from_numpy(
                    obj).to(device).float().unsqueeze(0)
                sim = F.cosine_similarity(output, affordances)
                sims.append(sim.item())
            #rank each candidate object based on the similarity value between
            #its embedding and the model's output embedding
            #(we want the model's output to be the most similar to the
            #correct object's embedding, as the model will select the object
            #with the embedding most similar to its output)
            sort = sorted(sims, reverse=True)
            if sims[0] == sort[0]:
                correct += 1
                correct2 += 1
                result = 'FIRST'
            elif sims[0] == sort[1]:
                correct2 += 1
                result = 'SECOND'
            else:
                result = 'BOTH WRONG'
            if opt.DEBUG:
                print()
                print(result)
            l = []
            for i, lt in enumerate(ret_objs):
                
                obj_name, aff, img = lt
                l.append([obj_name, sims[i], img])
            top1, top2 = sims.index(sort[0]), sims.index(sort[1])
            t1, t2 = ret_objs[top1][0], ret_objs[top2][0]
            if opt.DEBUG:
                print(s)
                print(output)
                print(l)
                print(t1,',', t2)
        print('RET_ACC Top1: {} Top2: {}'.format(
            correct/len(ret_set), correct2/len(ret_set)))


if opt.mode == 'YCB': #evaluation on YCB dataset
    aff_dict = {}
    with open(opt.input_embedding, 'r') as f:
        data = list(csv.reader(f))
        for row in data:
            #import pdb; pdb.set_trace()
            obj = str(row[0]).lower()
            aff = str(row[1])
            img = str(row[2])
            if obj not in aff_dict:
                aff_dict[obj] = []
            aff_dict[obj].append([aff, img])

    vo_dict = {}
    objects = []
    with open(opt.ycb_vo, 'r') as f:
        data = list(csv.reader(f))
        for row in data:
            verb = str(row[0]).lower()
            obj = str(row[1]).lower()
            if verb not in vo_dict:
                vo_dict[verb] = []
            if obj not in vo_dict[verb]:
                vo_dict[verb].append(obj)
            if obj not in objects:
                objects.append(obj)

    #exclude objects the model has already seen during training
    exclude = ['banana', 'strawberry', 'orange', 'pitcher base', 'plate', 'phillips screwdriver', 'flat screwdriver', 'hammer', 'baseball', 'toy airplane']
    #generate retrieval tasks (from the annotated verb-object pairs
    #and object embeddings from the YCB object set) to test the model
    ret_set = gen_ret(vo_dict, objects, aff_dict, word2id, id2word, exclude)
    ret(model, ret_set, id2word)


elif opt.mode == 'robot': #robot demo
    model.eval()
    with torch.no_grad():
        print(opt.command)
        command = process_command(opt.command, word2id, id2word)
        sentence = Tensor(command).unsqueeze(0)
        predicted = model(sentence)

        #use the pretrained resnet model to generate embeddings
        #for the object images captured by the robot
        embeddings = []
        for f in os.listdir(opt.image_dir):
            input_image = Image.open(os.path.join(opt.image_dir, f))
            input_tensor = preprocess(input_image)
            input_batch = input_tensor.unsqueeze(0)

            #move the input and model to GPU for speed if available
            if torch.cuda.is_available():
                input_batch = input_batch.to('cuda')
                model_avgpool.to('cuda')

            try:
                output = model_avgpool(input_batch)
            except:
                print('Cannot encode image', os.path.join(opt.image_dir, f))
            output = torch.flatten(output, 1)
            embeddings.append([f, output])

        sims = []
        for _, em in embeddings:
            sim = F.cosine_similarity(predicted, em)
            sims.append(sim.item())
        #rank each candidate object based on the similarity value between
        #its embedding and the model's output embedding, the model
        #will select the object with the embedding most similar to its output
        sort = sorted(sims, reverse=True)
        t1, t2, t3, t4, t5 = sims.index(sort[0]), sims.index(sort[1]), sims.index(sort[2]), sims.index(sort[3]), sims.index(sort[4])
        top1, top2, top3, top4, top5 = embeddings[t1][0], embeddings[t2][0], embeddings[t3][0], embeddings[t4][0], embeddings[t5][0]
        print(top1,',', top2,',', top3,',', top4,',', top5)


else: # evaluation on held-out test set
    vo_dict = None
    with open("data/vo_dict.json") as f:
        for line in f:
            vo_dict = json.loads(line)
    with open(opt.input_test, 'r') as test_file:
        test_data = list(csv.reader(test_file))
        test_dt = []
        for row in test_data:
            affordances = str(row[3])
            
            sentence = process_command(row[2], word2id, id2word)
            test_dt.append([row[0], row[1], sentence, affordances, row[4]])
        #generate retrieval tasks (from the held-out test data) to test the model
        ret_set = genRet(test_dt, vo_dict)
        ret(model, ret_set, id2word)