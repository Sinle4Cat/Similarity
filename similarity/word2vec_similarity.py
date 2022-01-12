# coding=utf-8
import os
import threading

import gensim
import threadpool
import jieba
import numpy as np
import tensorflow as tf
import xlrd
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from sklearn.metrics.pairwise import cosine_similarity

from similarity.bert_src.similarity_count import BertSim
from .vector_model import dim, model, model_path

catalogue_data_path = os.getcwd() + '/similarity/data/政务数据目录编制数据.xlsx'
bert_sim = BertSim()
bert_sim.set_mode(tf.estimator.ModeKeys.PREDICT)
catalogue_data_number = 12000
catalogue_data = []
catalogue_data_vector = []
bert_data = {}
process = 0
pool = threadpool.ThreadPool(10)


@csrf_exempt
@api_view(http_method_names=['get'])  # 只允许get
@permission_classes((permissions.AllowAny,))
def get_state(request):
    if process == 0:
        return Response({"code": 200, "msg": "模型和向量未初始化！", "data": ""})
    if process > 0.99:
        return Response({"code": 200, "msg": "模型和向量初始化完成！", "data": ""})
    return Response({"code": 200, "msg": "模型和向量初始化中！", "data": process})


@csrf_exempt
@api_view(http_method_names=['post'])  # 只允许post
@permission_classes((permissions.AllowAny,))
def init_model_vector(request):
    global process
    global catalogue_data_number
    global catalogue_data
    global catalogue_data_vector
    process = 0
    # 重新加载模型
    model = gensim.models.KeyedVectors.load_word2vec_format(model_path, binary=True)
    process = 0.5
    # 重新缓存向量
    catalogue_data = []
    catalogue_data_vector = []
    prepare_catalogue_data(path=catalogue_data_path)
    process = 0.75
    catalogue_data_number = len(catalogue_data)
    for i in range(len(catalogue_data)):
        process = 0.75 + i / (catalogue_data_number * 4)
        data = catalogue_data[i]
        item = data.split(' ')
        segment2_1 = jieba.lcut(item[2], cut_all=True, HMM=True)
        s2 = word_avg(model, segment2_1)
        catalogue_data_vector.append(s2)
    return Response({"code": 200, "msg": "词模型初始化完成；词向量缓存完成！", "data": ""})


@csrf_exempt
@api_view(http_method_names=['post'])  # 只允许post
@permission_classes((permissions.AllowAny,))
def multiple_match(request):
    parameter = request.data
    source_data = parameter['data']
    k = parameter['k']
    if len(catalogue_data) == 0:
        return HttpResponse("模型和向量未初始化")
    res = []
    for data in source_data:
        # 字符串匹配
        tmp = string_matching(demand_data=data, k=k)
        if len(tmp) != 0:
            res.append(tmp)
            continue
        # 查询BERT缓存
        tmp = find_data(demand_data=data, k=k)
        if len(tmp) != 0:
            res.append(tmp)
            continue
        # BERT缓存中不存在, 后台线程缓存
        th = threading.Thread(target=save_data, args=(data, k))
        th.start()

        # requests = threadpool.makeRequests(save_data, (data, k))
        # [pool.putRequest(req) for req in requests]

        # 缓存清理FIFO
        if len(bert_data.keys()) >= 10000:
            bert_data.clear()

        # 词向量匹配
        res.append(vector_matching(demand_data=data, k=k))
    return Response({"code": 200, "msg": "查询成功！", "data": res})


def string_matching(demand_data, k):
    res = []
    for data in catalogue_data:
        if demand_data in data.split(' ')[2]:
            res.append(data)
            if len(res) == k:
                break
    return res


def find_data(demand_data, k):
    if demand_data in bert_data.keys():
        tmp = bert_data.get(demand_data)
        if len(tmp) == k:
            return tmp
    return []


def save_data(demand_data, k):
    sim_words = {}
    for data in catalogue_data:
        sim = bert_sim.predict(data.split(' ')[2], demand_data)[0][1]
        if len(sim_words) < k:
            sim_words[data] = sim
        else:
            min_sim = min(sim_words.values())
            if sim > min_sim:
                for key in list(sim_words.keys()):
                    if sim_words.get(key) == min_sim:
                        # 替换
                        del sim_words[key]
                        sim_words[data] = sim
                        break
    res = []
    for sim_word in sim_words:
        res.append(sim_word)
    bert_data[demand_data] = res


def vector_matching(demand_data, k):
    # 字符串没有匹配项，则会进行向量相似度匹配，筛选前k个
    sim_words = {}
    segment1_1 = jieba.lcut(demand_data, cut_all=True, HMM=True)
    s1 = word_avg(model, segment1_1)
    for i in range(len(catalogue_data)):
        data = catalogue_data[i]
        # 计算整个信息项的相似度
        sim = cosine_similarity(s1.reshape(1, -1), catalogue_data_vector[i].reshape(1, -1))[0][0]
        if len(sim_words) < k:
            sim_words[data] = sim
        else:
            min_sim = min(sim_words.values())
            if sim > min_sim:
                for key in list(sim_words.keys()):
                    if sim_words.get(key) == min_sim:
                        # 替换
                        del sim_words[key]
                        sim_words[data] = sim
                        break
    if len(sim_words) == 0:
        return {'none': 0}
    res = []
    for sim_word in sim_words:
        res.append(sim_word)
    return res


def prepare_catalogue_data(path):
    # 打开excel
    wb = xlrd.open_workbook(path)
    # 按工作簿定位工作表
    sh = wb.sheet_by_name('信息资源导入模板')
    row_number = sh.nrows
    for i in range(2, row_number):
        catalogue_data.append(sh.cell(i, 0).value + ' ' + sh.cell(i, 3).value + ' ' + sh.cell(i, 11).value)


def word_avg(word_model, words):  # 对句子中的每个词的词向量简单做平均 作为句子的向量表示
    vectors = []
    for word in words:
        try:
            vector = word_model.get_vector(word)
            vectors.append(vector)
        except KeyError:
            vectors.append([0] * dim)
            continue
    return np.mean(vectors, axis=0)
