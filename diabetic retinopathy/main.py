import numpy as np
import argparse
import torch
import random
from torch.utils.data import DataLoader
from retina_dataset import Retina_Dataset
from torchvision.models import resnet18
from torchvision.models import resnet34
import torch
import torch.nn as nn
import torch.optim as optim
import copy
import torch.nn.functional as F
from torchsampler import ImbalancedDatasetSampler
import sklearn
from sklearn import metrics
from sklearn.metrics import confusion_matrix
from sklearn.metrics import roc_auc_score
from matplotlib import pyplot as plt

# import ray
# from ray import tune
# from ray.tune import track
# from ray.tune.schedulers import AsyncHyperBandScheduler

def train_epoch(net, args,train_loader,rounds,prev_model,epoch):
    # optimizer = optim.Adam(net.parameters(), lr=args.lr)
    optimizer = optim.Adam(net.parameters(), lr=args.lr, weight_decay=1e-7)
    criterion = nn.CrossEntropyLoss(reduction="sum").cuda()  
    loss_tmp = 0
    for iteration, data in enumerate(train_loader):
        inputs = data['image'].cuda()
        #.cuda()

        labels = data['label'].cuda()
        #.cuda()

        optimizer.zero_grad()

        outputs = net(inputs)

        if args.distillation_loss == 'yes' and rounds > 0:
            teacher_outputs = prev_model(inputs)
            loss = loss_fn_kd(outputs, labels.flatten(), teacher_outputs)
        else:
            loss = criterion(outputs, labels.flatten())
        #reg = 0
        #for param in net.parameters():
        #    reg += 0.5 * (param ** 2).sum() 
        #loss += reg * 0.001
        loss.backward()
        optimizer.step()
        loss_tmp += loss.cpu().data.numpy()

        print("\r[Epoch %2d][Step %4d/%4d] Loss: %.4f, Lr: %.2e" % (
                  epoch+1,
                  iteration,
                  int(len(train_loader.dataset) / args.batch_size),
                  loss_tmp / args.batch_size,
                  args.lr,# *[group['lr'] for group in optim.param_groups],
              ), end='          ')
    return net


def train_site(args, train_loader, eval_loader_dict, net, prev_model, round_num,best_net,best_performance,epoch):
    
    net.train(True)
    net = train_epoch(copy.deepcopy(net), args, train_loader, round_num, prev_model, epoch)
    net.train(False)
    full_labels = []
    unthreshold_pred =[]


    accuracy = []
    for j in range(round_num*args.sites+1,(round_num+1)*args.sites+1):     
        for iteration, data in enumerate(eval_loader_dict['val_loader' + str(j)]):
            inputs = data['image'].cuda()
            labels = data['label'].cuda()
            pred = net(inputs).cpu().data.numpy()
            labels = labels.cpu().data.numpy()
            unthreshold_pred += list(pred)
            full_labels += list(labels.flatten())
            accuracy += list(np.argmax(pred, axis=1) == labels.flatten())
    
    if args.val_auc == 'yes':
        unthreshold_pred = [p[1] for p in unthreshold_pred]
        auc = roc_auc_score(full_labels,unthreshold_pred)

        if auc > best_performance:
            best_performance = auc
            best_net = copy.deepcopy(net)

        print(auc)
    else:
        if 100 * np.nanmean(np.array(accuracy)) > best_performance:
            best_performance = 100 * np.nanmean(np.array(accuracy))
            best_net = copy.deepcopy(net)
        
        print(100 * np.nanmean(np.array(accuracy)))
        
    return net,best_net,best_performance
    

def roc_auc_score_multiclass(actual_class, pred_class, average = "macro"):

    #creating a set of all the unique classes using the actual class list
    unique_class = set(actual_class)
    roc_auc_dict = {}
    for per_class in unique_class:
        print(per_class)
        #creating a list of all the classes except the current class
        other_class = [x for x in unique_class if x != per_class]

        #marking the current class as 1 and all other classes as 0
        new_actual_class = [0 if x in other_class else 1 for x in actual_class]
        new_pred_class = [0 if x in other_class else 1 for x in pred_class]
        print(new_actual_class)
        print(new_pred_class)

        #using the sklearn metrics method to calculate the roc_auc_score
        roc_auc = roc_auc_score(new_actual_class, new_pred_class, average = average)
        roc_auc_dict[per_class] = roc_auc

    return roc_auc_dict


def test_round(test_loader,net):
    full_pred = []
    full_labels = []
    unthreshold_pred = []
    for iteration, data in enumerate(test_loader):
        inputs = data['image'].cuda()
        labels = data['label'].cuda().cpu().data.numpy()
        pred = net(inputs).cpu().data.numpy()
        full_pred += list(np.argmax(pred, axis=1))
        unthreshold_pred += list(pred)
        full_labels += list(labels.flatten())

    unthreshold_pred = [p[1] for p in unthreshold_pred]
    return full_pred, full_labels,unthreshold_pred


def get_accuracy(full_pred, full_labels,unthreshold_pred,round):
    # tn, fp, fn, tp = confusion_matrix(full_labels,full_pred).ravel()
    # print("True negative: " + str(tn))
    # print("False positive: " + str(fp))
    # print("False negative: " + str(fn))
    # print("True positive: " + str(tp))

    all_predictions = np.asarray(full_pred)
    all_labels = np.asarray(full_labels)
    all_unthreshold_pred = np.asarray(unthreshold_pred)

    accuracy = np.nanmean(all_labels == all_predictions)
    auc = roc_auc_score(all_labels,all_unthreshold_pred)

    
    accuracy_list = []
    auc_list = []
    for i in range(1000):
        indices = np.random.randint(0, len(all_labels)-1, len(all_labels))
        accuracy_test = np.nanmean(all_labels[indices] == all_predictions[indices])
        accuracy_list.append(accuracy_test)

        auc_test = roc_auc_score(all_labels[indices],all_unthreshold_pred[indices])
        auc_list.append(auc_test)

    fpr, tpr, _ = metrics.roc_curve(all_labels,all_unthreshold_pred)
    plt.plot(fpr, tpr, 'b', label = 'Patch-Based Model = %0.2f [0.86, 0.94]' % auc, color="blue")



    plt.legend(loc = 'lower right')
    plt.plot([0, 1], [0, 1],'r--')
    plt.xlim([0, 1])
    plt.ylim([0, 1])
    plt.ylabel('True Positive Rate')
    plt.xlabel('False Positive Rate')
    plt.savefig('round' + str(round) + '.png')



    auc_list.sort()
    accuracy_list.sort()
    return accuracy, accuracy_list[25], accuracy_list[-25],auc,auc_list[25],auc_list[-25]


def loss_fn_kd(outputs, labels, teacher_outputs):
    """
    Compute the knowledge-distillation (KD) loss given outputs, labels.
    "Hyperparameters": temperature and alpha
    NOTE: the KL Divergence for PyTorch comparing the softmaxs of teacher
    and student expects the input tensor to be log probabilities! See Issue #2
    """
    alpha = 0.5
    T = 3
    KD_loss = nn.KLDivLoss()(F.log_softmax(outputs/T, dim=1),
                             F.softmax(teacher_outputs/T, dim=1)) * (alpha * T * T) + \
              F.cross_entropy(outputs, labels) * (1. - alpha)

    return KD_loss

def parse_args():
    parser = argparse.ArgumentParser()
    # Model
    parser.add_argument('--model', type=str, default="resnet18")
    parser.add_argument('--dataloader', type=str, default="Retina_Dataset")
    parser.add_argument('--seed', type=int, default=random.randint(0, 9999999))
    parser.add_argument('--batch_size', type=int, default=32)
    parser.add_argument('--data_dir', type=str, default="data")
    parser.add_argument('--lr', type=float, default=0.0001)
    parser.add_argument('--classification',type=str,default = 'normal',help = "how many classes (binary or normal)")
    parser.add_argument('--sites',type=int,default = 1,help = "how many sites")
    parser.add_argument('--train_size',type=int,default = 1000, help = "how much train data each round")
    parser.add_argument('--positive_percent',type=float,default = 0.5,help = "what fraction of training data is positive")
    parser.add_argument('--switch_distribution',type=str,default = "no",help = "whether to switch the data distribution each round")
    parser.add_argument('--rounds',type=int,default = 1,help = "how many training rounds")
    parser.add_argument('--distillation_loss',type=str,default = 'no',help = "use distillation loss or not")
    parser.add_argument('--epochs_per',type=int,default=10,help = 'how many epochs per round')
    parser.add_argument('--model_save_path',type=str, default = 'model.pth',help = "where to save your model")
    parser.add_argument('--split',type=int, default = 0,help = "which split to use")
    parser.add_argument('--weighted_loss',type=str, default = 'no',help = "use weighted_loss or not")    
    parser.add_argument('--class_incremental',type=str, default = 'no',help = "class_incremental training or not")
    parser.add_argument('--val_auc',type=str, default = 'yes',help = "auc or not")

    args = parser.parse_args()
    return args




args = parse_args()


# Seed
torch.manual_seed(args.seed)
np.random.seed(args.seed)
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False

train_loader_dict = {}
for j in range(1,args.rounds*args.sites+1):
    key_train_loader = "train_loader" + str(j)
    dataset = eval(args.dataloader)('train', args, j,0)
    print(len(dataset))
    if args.weighted_loss == 'yes':
        data_loader = DataLoader(dataset, args.batch_size,num_workers=8, pin_memory=True)
        labels = []
        for iteration, data in enumerate(data_loader):
            labels.extend(list(data['label'].data.cpu().numpy()))
        labels = np.array(labels)
        class_sample_count = np.array(
            [len(np.where(labels == t)[0]) for t in np.unique(labels)])
        weight = 1. / class_sample_count
        samples_weight = np.array([weight[t] for t in labels])
        samples_weight= np.array([s for p in samples_weight for s in p])
        print(class_sample_count)
        sampler = torch.utils.data.WeightedRandomSampler(samples_weight, len(samples_weight))  
        train_loader_dict[key_train_loader] = DataLoader(dataset, args.batch_size,sampler=sampler,num_workers=8, pin_memory=True)
    else:
        train_loader_dict[key_train_loader] = DataLoader(dataset, args.batch_size,num_workers=8, pin_memory=True)


val_loader_dict = {}
for j in range(1,args.rounds*args.sites+1):
    key_val_loader = "val_loader" + str(j)
    dataset = eval(args.dataloader)('val', args, j,0)
    val_loader_dict[key_val_loader] = DataLoader(dataset, args.batch_size,num_workers=8, pin_memory=True)

test_loader_dict = {}
for j in range(1,args.rounds):
    key_test_loader = "test_loader" + str(j)
    dataset = eval(args.dataloader)('test', args, j,0)
    test_loader_dict[key_test_loader] = DataLoader(dataset, args.batch_size,num_workers=8, pin_memory=True)

test_final_loader = DataLoader(eval(args.dataloader)('test_final_loader', args,0,0), args.batch_size, num_workers=8, pin_memory=True)

net = eval(args.model)(pretrained=True)
net.fc = nn.Linear(512, 2)
#net.fc.register_forward_hook(lambda m, inp, out: F.dropout(out, p=0.4, training=m.training))
net.cuda()

for j in range(args.rounds):
    best_net = None
    best_performance = 0  
    net.train(True)
    prev_model = copy.deepcopy(net)
    print("Starting Training Round on Set " + str(j+1) + "...")
    for k in range(args.epochs_per):
        net,best_net,best_performance = train_site(args, train_loader_dict['train_loader' + str(args.sites*j+k%args.sites+1)], val_loader_dict, net, prev_model, j,best_net,best_performance,k)

    net = copy.deepcopy(best_net)

    net.train(False)

    if j != args.rounds - 1:
        full_pred, full_labels,unthreshold_pred = test_round(test_loader_dict['test_loader' + str(j+1)], net)
        accuracy, ci_low, ci_high,auc,auc_low,auc_high = get_accuracy(full_pred, full_labels,unthreshold_pred,j)
        print("Round " + str(j+1) + " Accuracy on Test " + str(j+1) + ": {} ({} - {})".format(accuracy, ci_low, ci_high))
        print("Round " + str(j+1) + " AUC_ROC on Test " + str(j+1) + ": {} ({} - {})".format(auc, auc_low, auc_high))


    full_pred, full_labels,unthreshold_pred = test_round(test_final_loader, net)
    accuracy, ci_low, ci_high,auc,auc_low,auc_high = get_accuracy(full_pred, full_labels,unthreshold_pred,j)
    print("Round " + str(j+1) + " Accuracy on Final Test: {} ({} - {})".format(accuracy, ci_low, ci_high))
    print("Round " + str(j+1) + " AUC_ROC on Final Test: {} ({} - {})".format(auc, auc_low, auc_high))









