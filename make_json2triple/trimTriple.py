import csv
import re

class TrimTriple():

    def run(self, input_tsv_path, output_tsv_path, isShuffle =False):
        print("Start Run")
        result = self._get_trim(input_tsv_path=input_tsv_path)
        result = self._shuffle(result=result, isShuffle=isShuffle)
        self._save(result=result, output_tsv_path=output_tsv_path)

    def _shuffle(self, result, isShuffle):
        if isShuffle:
            import random
            random.shuffle(result)
            print("shuffled")
        return result

    def _save(self, result, output_tsv_path):
        print("Saving : ", output_tsv_path)
        with open(output_tsv_path, 'w') as f_output:
            tsv_output = csv.writer(f_output, delimiter='\t')
            for row in result:
                tsv_output.writerow(row)

    def _is_number(self, s):
        try:
            float(s)
            return True
        except ValueError:
            return False

    def _cleanbracket(self, text, flag):
        if flag == 0:
            #모든 {{ }}
            cleanr = re.compile("{{[^}]*}}")
        elif flag == 1:
            #{{ }}내에 |가 1개 이상 존재
            cleanr = re.compile("{{[^|]*[|][^}]*}}")
        elif flag == 2:
            #( )을 모두 제거
            cleanr = re.compile("\([^\)]*\)")
        elif flag == 3:
            #{ }를 제거
            cleanr = re.compile("{[^}]*}")
        elif flag == 4:
            # { | }를 제거
            cleanr = re.compile("{{[^|]*[|][^}]*}")
        cleantext = re.sub(cleanr, '', text).strip()
        return cleantext

    def _cleanhtml(self, text):
      cleanr = re.compile('<.*?>')
      cleantext = re.sub(cleanr, '', text)
      return cleantext

    def _checkbracket(self, text, flag):
        """
        :param text: 내용
        :param flag: 1 = {{lang|en|abc}}의 경우 -> {{~}}을 리턴
                    2 = {{출생(사망)일|1900|1|1}}의 경우 -> {{~}}을 리턴
        :return:
        """
        if flag == 1: # {{lang|en|Charlie Chaplin}}
            output = []
            checkr = re.compile('{{lang[|][^|]*[|][^}]*}}')
            cleantext = re.findall(checkr, text)
            for txt in cleantext:
                output.append(txt.split("|")[-1][:-2])
        elif flag == 2: #{{~~~}
            checkr = re.compile('{{[^}]*}')
            output = re.findall(checkr, text)
        elif flag == 3: #{{국기나라|~~}}
            checkr = re.compile('{{국기나라[|][^}]*}}')
            output = re.findall(checkr, text)
        elif flag == 4: #{{국기그림|~~}
            checkr = re.compile('{{국기그림[|][^}]*}')
            output = re.findall(checkr, text)
        elif flag == 5: #{{~~~}
            checkr = re.compile('{{[^{}]*}')
            output = re.findall(checkr, text)
        return output

    def _get_trim(self, input_tsv_path):

        with open(input_tsv_path, 'r') as f:
            lines = list(csv.reader(f, delimiter="\t"))

        clean_output = []

        for line in lines:
            tag = line[1].split("/")[-1]
            target = line[2]
            if "\'\'" in target:
                cl_target = re.sub('\'', repl='', string=target)
                if cl_target == " ":
                    continue
                target = cl_target
            # Nothing to trim
            cld = self._checkbracket(target, flag=2)

            if line[1] == 'is_a':
                clean_output.append([line[0], line[1], line[2]])
            elif len(cld) is 0:
                if 'wiki:' in target:
                    clean_output.append([line[0], line[1], target])
            # {{출생 이 들어가는 경우 -> 54107가지
            elif "{{출생" in target:
                target = self._checkbracket(target, flag=2)
                if len(target) is 1:
                    target = target[0].rstrip("}").rstrip("|")
                    target_split = target.split("|")
                    # {{출생일|2020|10|10
                    if len(target_split) is 4:
                        year = target_split[1] + "년 " + target_split[2] + "월 " + target_split[3] + "일"
                        clean_output.append([line[0], line[1], year])
                    # {{출생일|2020
                    elif len(target_split) is 2:
                        clean_output.append([line[0], line[1], target_split[-1]])
                    # {{출생일|fm=ok|2020|10|24
                    elif len(target_split) > 4:
                        for i, tmp in enumerate(target_split):
                            if self._is_number(tmp):
                                year = target_split[i] + "년 " + target_split[i + 1] + "월 " + target_split[i + 2] + "일"
                                clean_output.append([line[0], line[1], year])
                                break
                    # {{출생일|2020|10
                    elif len(target_split) is 3:
                        year = target_split[1] + "년 " + target_split[2] + "월"
                        clean_output.append([line[0], line[1], year])
                    # {{출생일
                    else:
                        pass
                # {{출생일|1897|10|28}}, {{사망일과 나이|1984|11|28|1897|10|28}} -> {}가 2개 이상
                else:
                    for targ in target:
                        target_split = targ.split("|")
                        for i, tmp in enumerate(target_split):
                            if self._is_number(tmp):
                                year = target_split[i] + "년 " + target_split[i + 1] + "월 " + target_split[i + 2].rstrip(
                                    "}") + "일"
                                clean_output.append([line[0], line[1], year])
                                break
                        else:  # 아무것도 없음 {{출생일|YYYY|MM|DD}}, {{출생일과 나이|YYYY|MM|DD}}
                            pass
            # {{국기나라|노르웨이}} -> wiki:노르웨이 -> 28356가지
            elif "{{국기나라" in target:
                target = self._checkbracket(target, flag=3)
                for targ in target:
                    target_split = targ.split("|")
                    country = target_split[1].strip("}}")
                    clean_output.append([line[0], line[1].strip("ㅣ"), "wiki:" + country])
            # {{lang 이 들어가는 경우. -> 16782가지
            elif any(c in target for c in ["{{lang", "{{llang"]):
                cleaned_line = self._cleanhtml(target).strip("(").strip("\"").lstrip("〈").lstrip("《").lstrip("「")
                ##{{lang|en|Charlie Chaplin}} -> Charlie Chaplin
                if cleaned_line[:2] in ["{{"]:
                    for tmp in self._checkbracket(cleaned_line, flag=1):
                        clean_output.append([line[0], line[1], tmp])
                ##'소크라테스 ({{lang|grc|Σωκράτης}})' -> 소크라테스
                else:
                    tmp = None
                    tmp = (cleaned_line.split("{{"))[0].split("|")
                    tmp = tmp[0].rstrip("(").strip().rstrip("/").strip().rstrip("〉").rstrip("-").strip()
                    if tmp is not "":
                        clean_output.append([line[0], line[1], tmp])
            # {{사망 이 들어가는 경우 -> 8450가지
            elif "{{사망" in target:
                target = self._checkbracket(target, flag=2)
                if len(target) is 1:
                    target = target[0].rstrip("}")
                    target_split = target.split("|")
                    if len(target_split) > 3:
                        try:
                            for i, tmp in enumerate(target_split):
                                if self._is_number(tmp):
                                    year = target_split[i] + "년 " + target_split[i + 1] + "월 " + target_split[
                                        i + 2].rstrip("}") + "일"
                                    clean_output.append([line[0], line[1], year])
                                    break
                        except IndexError:
                            year = target_split[1].lstrip("양력").lstrip("음력").rstrip("년") + "년 " + target_split[
                                2] + "월 " + target_split[3].rstrip("}") + "일"
                            clean_output.append([line[0], line[1], year])
                    ##{{사망일|1897|1807}
                    elif (len(target_split) is 3) or (len(target_split) is 2):
                        if target_split[1] is not "":
                            clean_output.append([line[0], line[1], target_split[1]])
                # {{사망일|1897|10|28}}, {{사망일과 나이|1984|11|28|1897|10|28}} -> {}가 2개 이상
                else:
                    for targ in target:
                        target_split = targ.split("|")
                        for i, tmp in enumerate(target_split):
                            if self._is_number(tmp):
                                year = target_split[i] + "년 " + target_split[i + 1] + "월 " + target_split[i + 2].rstrip(
                                    "}") + "일"
                                clean_output.append([line[0], line[1].rstrip("|").strip(), year])
                                break
            # {{birth, {{Birth로 시작 ->53가지
            elif "{{birth" in target.lower():
                target = self._checkbracket(target, flag=2)
                if len(target) is 1:
                    target = target[0].rstrip("}").rstrip("|")
                    target_split = target.split("|")
                    # #{birth|2020|10|10
                    if len(target_split) is 4:
                        for i, tmp in enumerate(target_split):
                            if self._is_number(tmp):
                                year = target_split[i] + "년 " + target_split[i + 1] + "월 " + target_split[i + 2] + "일"
                                clean_output.append([line[0], line[1], year])
                                break
                    # #{{birth|2020
                    elif len(target_split) is 2:
                        clean_output.append([line[0], line[1], target_split[-1]])
                    # {{birth|fm=ok|2020|10|24
                    elif len(target_split) > 4:
                        for i, tmp in enumerate(target_split):
                            if self._is_number(tmp):
                                year = target_split[i] + "년 " + target_split[i + 1] + "월 " + target_split[i + 2] + "일"
                                clean_output.append([line[0], line[1], year])
                                break
                    # #{{birth|2020|10
                    elif len(target_split) is 3:
                        year = target_split[1] + "년 " + target_split[2] + "월"
                        clean_output.append([line[0], line[1], year])
                    # # {{birth
                    else:
                        clean_output.append(line)
                # {{birth|1897|10|28}}, {{birth 나이|1984|11|28|1897|10|28}} -> {}가 2개 이상
                else:
                    for targ in target:
                        target_split = targ.split("|")
                        for i, tmp in enumerate(target_split):
                            if self._is_number(tmp):
                                year = target_split[i] + "년 " + target_split[i + 1] + "월 " + target_split[i + 2].rstrip(
                                    "}") + "일"
                                clean_output.append([line[0], line[1], year])
                                break
                        else:  # 아무것도 없음 {{출생일|YYYY|MM|DD}}, {{출생일과 나이|YYYY|MM|DD}}
                            pass
            # {{국기그림|대한민국}} ~~ -> 17000가지
            elif "{{국기그림" in target:
                # {{국기그림|대한민국}}
                if tag in ["country", "nationality", "birthPlace"]:
                    target = self._checkbracket(target, flag=4)
                    for targ in target:
                        target_split = targ.split("|")
                        clean_output.append([line[0], line[1], "wiki:" + target_split[1].strip("}")])
                # 이름 {{국기이름|대한민국}} , {{국기이름|대한민국}} 이름
                elif tag == "foaf:name":  # 49
                    if target[0] == "{":
                        target_split = target.split("}}")
                        clean_output.append([line[0], line[1], "wiki:" + target_split[1].strip()])
                    else:
                        target_split = target.split("{{")[0].split("|")
                        clean_output.append([line[0], line[1], "wiki:" + target_split[0].strip()])
                # {{국기그림|일본}} {{isbn|1234}}
                elif tag == 'isbn':  # 74
                    target = self._checkbracket(target, flag=2)
                    if len(target) == 1:
                        clean_output.append([line[0], line[1], line[2].split("}}")[1]])
                    else:
                        for targ in target:
                            target_split = targ.split("|")
                            if target_split[0] == "{{국기그림":
                                continue
                            else:
                                clean_output.append([line[0], line[1], target_split[1].strip()])
                ##{{국기그림|영국}} {축구단 첼시}
                elif tag in ["club", "managerClub", "youthClub", "team"]:  # 43
                    clean_target = self._cleanbracket(target, 1)
                    target = self._checkbracket(clean_target, flag=2)
                    if len(target) is 0:
                        target = self._cleanbracket(self._cleanhtml(clean_target), flag=2)
                        clean_output.append([line[0], line[1], target.lstrip("→").strip()])
                    else:
                        clean_output.append([line[0], line[1], "wiki:" + target[0].strip().lstrip("{{").rstrip("}")])
                elif tag == "foaf:homepage":
                    pass
                    # org.append(line)
                    # org2.append(target)
                elif tag == "releaseDate":
                    target = self._cleanbracket(target, 0)
                    target = self._cleanbracket(self._cleanhtml(target), flag=2).strip()
                    if len(target) == 1:
                        continue
                    elif target == '':
                        target = self._checkbracket(line[2], flag=2)
                        for targ in target:
                            target_split = targ.split("|")
                            if target_split[0] == "{{국기그림":
                                continue
                            else:
                                try:
                                    clean_output.append([line[0], line[1],
                                                         target_split[1] + "년 " + target_split[2] + "월 " + target_split[
                                                             3].strip("}") + "일"])
                                except IndexError:
                                    clean_output.append([line[0], line[1], target_split[1].strip("}")])
                    else:
                        clean_output.append([line[0], line[1], target])
                else:
                    target = self._cleanbracket(target, 0)
                    target = target.split("<br />")[0]
                    target = self._cleanbracket(self._cleanhtml(target), flag=2).strip()
                    if target == '':
                        target = self._checkbracket(line[2], flag=2)
                        target = target[-1].split("|")
                        if target[0] == "{{국기그림":
                            clean_output.append([line[0], line[1], "wiki:" + target[1].strip("}")])
                        else:
                            clean_output.append([line[0], line[1], target[-1].strip("}")])
                    else:
                        clean_output.append([line[0], line[1], target.strip("→").strip()])
            # {{국기|대한민국}} -> 14556가지
            elif "{{국기" in target:
                tag = line[1].split("/")[-1]
                if tag in ['country', 'nationality']:
                    target = self._checkbracket(target, flag=2)
                    for targ in target:
                        target_split = targ.split("|")
                        if len(target_split) == 1:
                            clean_output.append([line[0], line[1], target_split[0]])
                        else:
                            clean_output.append([line[0], line[1], target_split[1]])
                else:
                    target = self._checkbracket(target, flag=5)
                    for targ in target:
                        target_split = targ.split("|")
                        if len(target_split) == 1:
                            clean_output.append([line[0], line[1], "wiki:" + target_split[0].strip("}")])
                        else:
                            if target_split[0] == "{{llang":
                                clean_output.append([line[0], line[1], target_split[2].strip("}")])
                            else:
                                clean_output.append([line[0], line[1], "wiki:" + target_split[1].strip("}")])
            # {{노벨상 딱지 -> 570가지
            elif "{{노벨상" in target:
                if "{{노벨상 딱지}" in target:
                    target = self._cleanbracket(target, flag=0)
                    if len(target) is 0:
                        continue
                    else:
                        clean_output.append([line[0], line[1], target])
                else:
                    clean_output.append([line[0], line[1], "wiki:노벨 평화상"])
            # {{한국 지하철}} -> 2000가지
            elif "{{한국" in target:
                if any(c in tag for c in ['Code', '코드', '단말기']):
                    target_split = target.split(":")
                    clean_output.append(
                        [line[0], 'http://dbpedia.org/ontology/agencyStationCode', target_split[1].strip("}")])
                else:
                    target = self._checkbracket(target, flag=5)
                    target_split = target[0].split("|")
                    clean_output.append([line[0], line[1], target_split[1].strip("}")])
            # 이상한 exception값들 -> 20가지
            elif "{{출처" in target:
                target = self._cleanbracket(self._cleanbracket(self._cleanhtml(target), flag=0), flag=2)
                if target == '':
                    continue
                else:
                    clean_output.append([line[0], line[1], target])
            # tag를 기준 -> 23600가지
            elif any(c in tag for c in ["country", 'nationality', '나라']):
                target = self._checkbracket(target, flag=5)
                for targ in target:
                    target_split = targ.split("|")
                    if len(target_split) == 1:
                        clean_output.append([line[0], line[1], "wiki:" + target_split[0].lstrip("{{").rstrip("}")])
                    else:
                        clean_output.append([line[0], line[1], "wiki:" + target_split[1].rstrip("}")])
            elif any(c in tag for c in ["각주", 'caus', '좌표', 'icd']):
                continue
            elif any(c in target for c in ['좌표']):
                continue
            elif 'date' in tag.lower():
                target = self._checkbracket(target, flag=2)
                if len(target) is 1:
                    target_split = target[0].split("|")
                    if len(target_split) is 4:
                        year = target_split[1] + "년 " + target_split[2] + "월 " + target_split[3].strip("}") + "일"
                        clean_output.append([line[0], line[1], year])
                    elif len(target_split) is 2:
                        clean_output.append([line[0], line[1], target_split[-1].strip("}")])
                    elif len(target_split) is 3:
                        year = target_split[1] + "년 " + target_split[2].strip("}") + "월"
                        clean_output.append([line[0], line[1], year])
                    elif len(target_split) is 1:
                        tmp = self._cleanbracket(line[2], flag=0)
                        if tmp == "":
                            clean_output.append([line[0], line[1], target_split[0].lstrip("{{").rstrip("}")])
                        else:
                            clean_output.append([line[0], line[1], tmp])
                    elif len(target_split) > 4:
                        for i, tmp in enumerate(target_split):
                            if self._is_number(tmp):
                                year = target_split[i] + "년 " + target_split[i + 1] + "월 " + target_split[i + 2].strip(
                                    "}") + "일"
                                clean_output.append([line[0], line[1], year])
                                break
                else:
                    target = self._cleanhtml(self._cleanbracket(cl_target, flag=1)).strip("}")
                    clean_output.append([line[0], line[1], target])
                """
                #{{출생일|1897|10|28}}, {{사망일과 나이|1984|11|28|1897|10|28}} -> {}가 2개 이상
                else:
                    for targ in target:
                        target_split = targ.split("|")
                        for i, tmp in enumerate(target_split):
                            if is_number(tmp):
                                year = target_split[i]+"년 "+target_split[i+1]+"월 "+target_split[i+2].rstrip("}")+"일"
                                clean_output.append([line[0], line[1], year])
                                break
                        else:   # 아무것도 없음 {{출생일|YYYY|MM|DD}}, {{출생일과 나이|YYYY|MM|DD}}
                            pass
                """
            elif "http" in target:
                target = self._cleanbracket(target, flag=0)
                if target == '':
                    target_split = line[2].split("|")
                    if target_split[1] != "":
                        clean_output.append([line[0], line[1], target_split[1].strip("}")])
                else:
                    target = self._cleanbracket(self._cleanhtml(self._cleanbracket(target, flag=3)), flag=2).rstrip("]").lstrip("[")
                    if 'http' not in target:
                        continue
                    else:
                        clean_output.append([line[0], line[1], target])
            elif "날짜" in target:
                target = self._checkbracket(self._cleanhtml(target), flag=2)
                if len(target) == 0:
                    continue
                elif len(target) is 1:
                    target_split = target[0].split("|")
                    if len(target_split) is 4:
                        year = target_split[1] + "년 " + target_split[2] + "월 " + target_split[3].strip("}") + "일"
                        clean_output.append([line[0], line[1], year])
                    elif len(target_split) is 2:
                        clean_output.append([line[0], line[1], target_split[-1].strip("}")])
                    elif len(target_split) is 3:
                        year = target_split[1] + "년 " + target_split[2].strip("}") + "월"
                        clean_output.append([line[0], line[1], year])
                    elif len(target_split) > 4:
                        for i, tmp in enumerate(target_split):
                            if self._is_number(tmp):
                                year = target_split[i] + "년 " + target_split[i + 1] + "월 " + target_split[i + 2].strip(
                                    "}") + "일"
                                clean_output.append([line[0], line[1], year])
                                break
                else:
                    target = self._cleanhtml(self._cleanbracket(cl_target, flag=1)).strip("}")
                    target = self._cleanbracket(target, flag=0).strip()
                    clean_output.append([line[0], line[1], target])
            elif any(c in tag for c in ["시장 정보", "시장정보"]):
                target = self._checkbracket(target, flag=2)
                for targ in target:
                    target_split = targ.split("|")
                    if len(target_split) == 1:
                        if len(target) < 3:
                            target = target[0].split("|")
                            for i in range(len(target)):
                                clean_output.append([line[0], line[1], target[i].upper().strip("{{").strip("}")])
                        else:
                            continue
                    elif len(target_split) == 2:
                        clean_output.append([line[0], line[1], target_split[0].upper().strip("{{")])
                        clean_output.append([line[0], line[1], target_split[1].upper().strip("}")])
                    else:
                        if "{{기호" in target_split[0]:
                            for i in range(1, len(target_split)):
                                clean_output.append([line[0], line[1], target_split[i].strip("{{").strip("}")])
                        else:
                            clean_output.append([line[0], line[1], target_split[0].upper().strip("{{")])
                            clean_output.append([line[0], line[1], target_split[1].upper().strip("}")])
            else:
                clean_target = self._cleanbracket(target, flag=0)
                if clean_target == "":
                    target_split = target.split("|")
                    if len(target_split) == 1:
                        clean_output.append([line[0], line[1], "wiki:" + target_split[0].lstrip("{{").rstrip("}}")])
                    elif len(target_split) == 2:
                        tmp = target_split[1].rstrip("}}")
                        if len(tmp) < 7:
                            clean_output.append([line[0], line[1], "wiki:" + tmp])
                        else:
                            clean_output.append([line[0], line[1], tmp])
                    elif len(target_split) == 3:
                        clean_output.append([line[0], line[1], target_split[1]])
                    elif len(target_split) == 4:
                        clean_output.append([line[0], line[1], self._cleanhtml(target_split[2])])
                    else:
                        clean_output.append([line[0], line[1], self._cleanhtml(target_split[1])])
                else:
                    target = self._cleanbracket(self._cleanbracket(self._cleanhtml(target), flag=0).split("|")[0], flag=2).strip(
                        "{").strip("}").strip()
                    if not target == "":
                        clean_output.append([line[0], line[1], target])

        result = []
        for i in range(len(clean_output)):
            if clean_output[i][1] == 'is_a':
                result.append([clean_output[i][0], clean_output[i][1], clean_output[i][2]])
            elif 'wiki:' in clean_output[i][2]:
                result.append([clean_output[i][0], clean_output[i][1], clean_output[i][2]])

        return result

if __name__ == "__main__":

    trimTriple = TrimTriple()
    trimTriple.run(input_tsv_path= "../resources/full_output.tsv", output_tsv_path="../resources/full_output_trim.tsv", isShuffle=False)

    print("1")