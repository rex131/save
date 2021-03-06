import os
import csv
import json
import torch
import torch.nn as nn
import torchvision.models as models
from PIL import Image
from torchvision import transforms
import clip
'''
Use the CLIP to generate object-embedding pairs corresponding to each given ImageNet image. (the model is changed from RESNET to CLIP)
'''

#-----LOAD MODEL-----#
#model = models.resnet50(pretrained=True)
#model = models.resnet101(pretrained=True)
#model = models.resnet152(pretrained=True)

# Access average pooling layer in network
#model_avgpool = nn.Sequential(*list(model.children())[:-1])
#model_avgpool.eval()
""" preprocess = transforms.Compose([
    transforms.Resize(256),
    transforms.CenterCrop(224),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
]) """


#-----USE MODEL-----#
labels = {}
with open('data/clip-object-embedding.csv', 'w', newline='') as out:
    #below file is generated by imagenet.py
    with open('data/image_label.json') as data:
        csvwriter = csv.writer(out, delimiter=',')
        for line in data:
            image_label = json.loads(line)
        #substitute with your own path to the ILSVRC2012 validation image set
        dir = '/home/turtlepc-04/Downloads/ILSVRC2012_img_val'
        for f in os.listdir(dir):
            #image_input1 = preprocess(image1).unsqueeze(0).to(device)
            device = "cuda:0" if torch.cuda.is_available() else "cpu"

            model, preprocess = clip.load('RN50', device)
            input_image = Image.open(os.path.join(dir, f))
            input_tensor = preprocess(input_image)
            input_batch = input_tensor.unsqueeze(0).to(device) # create a mini-batch as expected by the model
            with torch.no_grad():
                   output = model.encode_image(input_batch)
            # move the input and model to GPU for speed if available
            """ if torch.cuda.is_available():
                input_batch = input_batch.to('cuda')
                model_avgpool.to('cuda')
 """
            """ with torch.no_grad():
                try:
                    output = model_avgpool(input_batch)
                except:
                    print(os.path.join(dir, f)) """
            output = torch.flatten(output, 1)
            try:
                   csvwriter.writerow((image_label[f], output[0].tolist(), f))
            except: Exception