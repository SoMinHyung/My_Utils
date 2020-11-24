import os, sys
import re
import csv
import json
import xml.etree.ElementTree as ET
import random
from owlready2 import *


class Infobox2Triple():

    def __init__(self):
        self.abs_path = os.path.dirname(os.path.abspath(__file__))

    def run(self, input_json_path, output_tsv_path, isShuffle=False):
        #이 py파일과 xml폴더를 같은 디렉토리 하에 둬야 함.
        total_dict = self._get_xml()
        self.result = self._make_triple_json(filepath=input_json_path, total_dict=total_dict)
        self._shuffle(isShuffle)
        self._save(save_path=output_tsv_path)


    def _save(self, save_path):
        # save result as tsv
        with open(save_path, 'w') as f_output:
            tsv_output = csv.writer(f_output, delimiter='\t')
            for row in self.result:
                tsv_output.writerow(row)


    def _shuffle(self, shuffle):
        #학습을 위해 random shuffle
        if shuffle:
            random.shuffle(self.result)
            print("shuffled")


    def _get_MappingDict(self, filepath):
        doc = ET.parse(filepath)
        title = ""
        class_name = []
        dict = {}

        for ls in doc.find('{http://www.mediawiki.org/xml/export-0.8/}page'):
            flag = ls.tag[42:]

            # 해당 개체의 Mapping Title을 찾음. Type : string
            if flag == "title":
                title = ls.text[11:]
            elif flag == "revision":
                total_txt = ls.find('{http://www.mediawiki.org/xml/export-0.8/}text').text

                # 해당 개체의 class값들을 반환 Type : List
                map2class = re.findall(r"mapToClass = [a-zA-Z]*", total_txt)
                for m2c in map2class:
                    class_name.append(m2c[13:])

                # 해당 개체의 property값들을 Type : dict
                search = ["mappings", "end}}"]
                map_txt = total_txt[total_txt.lower().find(search[0]):total_txt.lower().find(search[1])]
                map_txt = map_txt[10:]
                map_txt = map_txt.strip("\n")
                mapping_list = map_txt.split("}}\n")

                for map in mapping_list:
                    map = map.lstrip().strip("\t")
                    reg = re.compile(r"([\sa-zA-z가-힣:0-9\)\(\-]*) ")
                    out = re.findall(reg, map)
                    # print(map)
                    if map[2] in ["P", "C"]:
                        dict[out[2].strip()] = out[4].strip()
                        if len(out) > 5:
                            dict[out[2].strip()] = [out[4].strip(), out[-1].strip()]
                    elif map[2] == "D":
                        dict[out[2].strip().strip("\n")] = \
                            [out[-3].strip().strip("\n"), out[-1].strip().strip("\n")]
                    elif map[2] == "G":
                        if len(out) > 5:
                            for t in range(2, len(out), 2):
                                dict[out[t].strip()] = out[t - 1].strip()
                        else:
                            dict[out[2].strip()] = out[1].strip()

        return title, class_name, dict


    def _get_xml(self):
        total_dict = {}
        for filepath in os.listdir(os.path.join(self.abs_path, "mapping_xml")):
            if ".xml" in os.path.basename(filepath):
                title, class_name, dict = self._get_MappingDict(os.path.join(self.abs_path, "mapping_xml", filepath))
                total_dict[title] = {"property": 0, "class": 0}
                total_dict[title]["property"] = dict
                total_dict[title]["class"] = class_name

        return total_dict


    def _find_bracket(self, key, value):
        """
        [[ ]]안에 있는 object값 찾기.
            1) | 앞에 있는 값만 가져옴             ex) '[[미국]] [[조지아주]] [[플레인스 (조지아)|플레인스]]' -> 미국, 조지아주, 플레인스 (조지아)
            2) [[ ]]와 연결된 텍스트는 그냥 버림.   ex) '[[조지아 주]] 의회' -> 조지아 주
        """
        temp = []
        if value.find("[[") != -1:
            if key in ["시장 정보", "시장정보"]:
                temp.append(value)
            else:
                value = re.findall((r'[\[\[]+[-_.|()\s0-9a-zA-Z가-힣]*[\]]+'), value)
                for val in value:
                    if ("|") in val:
                        val = val.split("|")[0]
                    if "[[" in val:
                        val = val[2:]
                    if "]]" in val:
                        val = val[:-2]
                    val = "wiki:" + val
                    temp.append(val)
        else:
            temp.append(value)

        return temp


    def _split_by_br(self, split_value):
        # <br />로 구분된 대상들을 분리해줌.
        tmp = []
        for i in range(len(split_value)):
            tokens = []
            if '<br />' in split_value[i]:
                tokens = split_value[i].split("<br />")
                for token in tokens:
                    tmp.append(token.strip())
            elif '<br/>' in split_value[i]:
                tokens = split_value[i].split("<br/>")
                for token in tokens:
                    tmp.append(token.strip())
            elif '<br>' in split_value[i]:
                tokens = split_value[i].split("<br>")
                for token in tokens:
                    tmp.append(token.strip())
            else:
                tmp.append(split_value[i])

        return tmp


    def _erase_tag(self, split_value):
        # ref태그를 제거
        tmp = []
        for i in range(len(split_value)):
            if '<ref' in split_value[i]:
                token = split_value[i]
                if '</ref>' in token:
                    if len(token) == token.find("</ref>") + 6:
                        token = token.strip("</ref>")
                        token = token[:token.find("<ref")]
                        tmp.append(token.strip())
                    else:
                        tokens = token.split("</ref>")
                        for token in tokens:
                            if "<ref" in token:
                                tmp.append(token[:token.find("<ref")])
                            else:
                                tmp.append(token.strip())
                else:
                    token = token[:token.find("<ref")]
                    tmp.append(token.strip())
            else:
                tmp.append(split_value[i])

        return tmp


    def _make_triple_json(self, filepath, total_dict):
        with open(filepath, 'r') as f:
            wiki_dict_data = json.load(f)
            print(len(wiki_dict_data))    #full = 267299 , mini = 5299

        # read owl
        ontology = get_ontology("file://"+ os.path.dirname(self.abs_path) + "/resources/dbpedia_3.9.owl").load()

        output = []
        market_output = []

        for i, one_wiki in enumerate(wiki_dict_data):
            category = one_wiki['categ']
            title = "wiki:" + one_wiki["main_title"]

            try:
                dic = total_dict[category] #dic -> Wiki에 일치하는 mapping_table
                #title과 category는 삭제.
                [one_wiki.pop(out, None) for out in ["main_title", "categ"]]

                #ontology의 클래스 정보 추가
                class_list = None
                self_class = dic["class"][0]
                class_list = ontology[self_class].ancestors()
                for class_ in class_list:
                    if str(class_).startswith("dbpedia"):
                        output.append([title, "is_a", str(class_).split("dbpedia_3.9.")[1]])
                    else:
                        continue

                for key, value in one_wiki.items():

                    # [[ ]]안에 있는 object값 찾기.
                    split_value = self._find_bracket(key, value)

                    # <br />로 구분된 대상들을 분리해줌.
                    split_value = self._split_by_br(split_value)

                    #ref태그를 제거
                    split_value = self._erase_tag(split_value)

                    # # 시장 정보(종목 코드)만 찾기
                    # for i in range(len(split_value)):
                    #     if not split_value[i] == "":
                    #         #if dic["property"][key]
                    #         if key in ["시장 정보", "시장정보"]:
                    #
                    #             if key in dic["property"].keys():
                    #                 if isinstance(dic["property"][key], list):
                    #                     market_output.append([title, "http://dbpedia.org/ontology/" + dic["property"][key][0], str(split_value[i])])
                    #                 else:
                    #                     market_output.append([title, "http://dbpedia.org/ontology/" + dic["property"][key], str(split_value[i])])
                    #             else:
                    #                 market_output.append([title, "http://ko.dbpedia.org/property/" + key, str(split_value[i])])

                    #정의된 온톨로지와 아닌 온톨로지를 분류.
                    for i in range(len(split_value)):
                        if not split_value[i] == "":
                            #정의된 온톨로지는 dbpedia tag
                            if key in dic["property"].keys():
                                if isinstance(dic["property"][key], list):
                                    output.append([title, "http://dbpedia.org/ontology/" + dic["property"][key][0], str(split_value[i])])
                                else:
                                    output.append([title, "http://dbpedia.org/ontology/" + dic["property"][key], str(split_value[i])])
                            #정의 안된 것들은 ko.
                            else:
                                output.append([title, "http://ko.dbpedia.org/property/" + key, str(split_value[i])])

                print(title," finished")


            except KeyError as e:
                pass
                # TODO: mapping_table이 없는 대상들도 추후에 KG에 추가할 예정. -> mapping_table만들어야함.
                """
                no_dict[category] += 1
                print(f"No {title} in mapping_xml")
                
                #mapping_table이 없는 대상 중에서 많이 사용된 순서대로 정렬. 
                no_dict =  {k: v for k, v in sorted(no_dict.items(), key=lambda item: item[1], reverse=True)}
                for k, v in no_dict.items():
                    print("key : ",k)
                    print("val : ", v)
                    print("total : ", sum)
                """

        return output

if __name__ == '__main__':
    info2triple = Infobox2Triple()
    info2triple.run(input_json_path = "../resources/full_output.json", output_tsv_path = '../resources/full_output.tsv', isShuffle=False)
    print("finish")