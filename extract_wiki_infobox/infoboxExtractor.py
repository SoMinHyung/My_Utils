import xml.etree.ElementTree as ET
import json
import time

def save_file(parsed_data, output_file_name):
    with open(output_file_name, 'w') as file:
        json.dump(parsed_data, file, ensure_ascii=False)
    print("Wikidump parsed and saved at " + output_file_name)

class InfoboxExtractor():

    def run(self, input_file_name):
        parsed_data=self._parse_dump(input_file_name)
        return parsed_data

    def _unmatched_bracket(self, text):
        """
        Returns true if there is an unmatched bracket
            this is a sentence {with a bracket } - false
            this is a sentence {with a bracket } and {this - true
        """
        for c in reversed(text):
            if c is "}":
                return False
            elif c is "{":
                return True

    def _find_infobox(self, page_text, end_index):
        """
        하나의 본문page에서 infobox의 시작과 끝 위치를 찾습니다.

        :param page_text: 본문 내용
        :param end_index: infobox가 끝나는 지점.
        :return: infobox의 시작점, 끝점, 추가 infobox의 유무를 리턴.
        """
        num_line = 0
        patience = 4  # 초반 몇개의 {}까지 체크를 해볼것인가. default =4개
        # {가 시작했는지를 체크.
        change_line = False
        # { }\n 같은경우를 배제하기 위한 플래그. {가 열려있으면서 줄바꿈을 했어야 infobox가 시작했다고 체크.
        start_flag = False
        bracket_count = 0
        more_infobox = False

        start_index = end_index

        # infobox의 끝을 체크 & {로 시작해도 infobox가 아닌 경우는 제외
        for i in range(end_index, len(page_text)):
            char = page_text[i]
            # infobox가 시작한 경우 -> {{ ~~~ \n 인 경우.
            if change_line is True:
                if char is "}":
                    bracket_count -= 1
                elif char is "{":
                    bracket_count += 1
                if bracket_count is 0:
                    end_index = i + 1
                    try:
                        #}가 닫히자마자 바로 다음에 {가 시작하는 경우. infobox를 더 읽어옴.
                        #하나의 페이지에서 {infobox1}{infobox2 와 같이 infobox가 여러개인 경우가 있음.
                        if page_text[end_index + 1] == "{":
                            for j in range(1, 50):
                                char2 = page_text[end_index + j]
                                if char2 is "}":
                                    #50글자 안에 }가 닫히는 경우, infobox가 아님.
                                    return start_index, end_index, more_infobox
                                elif char2 is "\n":
                                    #50글자 안에 \n과 함께 줄이 바뀌는 경우, 추가 infobox가 존재.
                                    more_infobox = True
                                    return start_index, end_index, more_infobox

                        return start_index, end_index, more_infobox
                    except IndexError as e:
                        pass
            # infobox가 시작안한 경우.
            else:
                #patience를 넘어가면 중단.
                if num_line > patience:
                    break
                #{ } 개수를 세서 0이 되도록 체크
                if char is "}":
                    bracket_count -= 1
                elif char is "{":
                    bracket_count += 1
                    start_flag = True
                #줄을 바꾸면서 { }가 끝났으면 카운팅+1, 시작글자를 변경
                elif char is "\n":
                    #{}가 닫혔으면서 \n이면 시작지점을 다음으로 변경. -> 시작 문장내에서 { }가 닫힌경우임. infobox앞에 {}가 붙어있는 경우가 가끔 있음.
                    if bracket_count is 0:
                        start_index = i + 1
                    #\n이면서 {로 시작했으면 infobox가 시작했다고 체크.
                    elif start_flag:
                        change_line = True
                    num_line += 1

        return start_index, end_index, more_infobox

    def _get_infobox2dict(self, infobox_sentence_list, page_title):
        first_sentence_split = infobox_sentence_list[0].split("|", 1)
        infobox_category = first_sentence_split[0][2:].strip()
        infobox = None

        # infobox가 사진인 경우 제외 -> None으로 리
        if infobox_category in ["imbox", "fmbox", "여러그림", "위치 지도+", "중앙", "여러 문제", "여러문제"]:
            return infobox

        # 첫 문장에 |가 있는 경우.
        if len(first_sentence_split) == 2:
            #{{인물정보|
            if first_sentence_split[1].strip() == '':
                infobox_sentence_list = infobox_sentence_list[1:]
            #{{인물정보|이름=조지4세
            elif "=" in first_sentence_split[1]:
                del infobox_sentence_list[0]
                for sent in first_sentence_split[1].split("|"):
                    infobox_sentence_list.insert(0,"|"+sent)
                    #infobox_sentence_list[0] = "|" + first_sentence_split[1]
            #{{산맥정보|(지리유형:산맥)
            else:
                infobox_sentence_list[0] = "|" + first_sentence_split[1]
        # 일반적인 경우
        else:
            infobox_sentence_list = infobox_sentence_list[1:]

        infobox_merged_content = []
        for line in infobox_sentence_list:
            line = line.strip()
            #공백 삭제
            if len(line) is 0:
                continue
            #}로 시작하는 문장이나 <!--***제목***--> 삭제
            elif line[0] in ["<", "}"]:
                continue
            elif line[0] is "|":
                infobox_merged_content.append(line)
            else:
                try:
                    infobox_merged_content[-1] += " " + line
                except IndexError as e:
                    if line.strip()[-1] is "|":
                        line = "|" + line[:-1]
                    infobox_merged_content.append(line)

        infobox = {"main_title": page_title}
        infobox["categ"] = infobox_category
        for entry in infobox_merged_content:
            key_data = entry.strip().split("=", 1)
            key = key_data[0].lstrip()[1:].strip()

            if len(key) > 0 and key[0] is "|":
                # eg: ||NotParticipating=Stewart and Fortas
                key = key[1:]

            data = ""

            if len(infobox) > 0 and self._unmatched_bracket(infobox[list(infobox.keys())[-1]]):
                infobox[list(infobox.keys())[-1]] += " " + key
                continue
            if len(key_data) == 2:
                data = key_data[1].strip()
            infobox[key] = data

        return infobox


    def _parse_dump(self, inputPath):
        tree = ET.parse(inputPath)
        root = tree.getroot()
        ns = 'http://www.mediawiki.org/xml/export-0.10/'

        infoboxes = []
        no_info = 0
        no_bracket_info = 0
        frame_info = 0
        script_page = 0

        #dump의 페이지를 하나씩 가져옴.
        for page in root.findall('{%s}page' % ns):
            page_title = page.find('{%s}title' % ns).text
            page_text = page.find('{%s}revision' % ns).find('{%s}text' % ns).text

            #제목이 없는 경우는 스
            if page_title == "":
                print("no named")
                continue
            #틀 정보는 스킵.
            if "틀:" in page_title:
                frame_info +=1
                continue

           #첫 { 찾기 - infobox는 무조건 text맨 앞에서 {로 시작함.
            try:
                bracket_start = page_text.find("{")
                #페이지에 {가 있지만, infobox가 아닌 본문에 있는 경우도 스킵.
                if bracket_start is not 0:
                    no_info += 1
                    continue
            #페이지에 아무것도 없는 경우.
            except AttributeError as e:
                print(e, " in ", page_title)    #-> 17
                no_info += 1
                continue

            #해당 페이지의 infobox 시작과 끝 위치를 찾아옴.
            start_index = 0
            end_index = 0
            more_infobox = True
            infobox_list = []

            #해당 페이지에 있는 모든 infobox를 읽어옴. 추가 infobox가 있다면 더 읽어옴. 대부분의 경우 1번만 실행됨.
            while more_infobox:
                start_index, end_index, more_infobox = self._find_infobox(page_text, end_index)
                infobox_list.append(page_text[start_index:end_index])

            for infobox_str in infobox_list:
                infobox_sentence_list = infobox_str.splitlines()

                if len(infobox_sentence_list) == 0 or infobox_sentence_list[0][:2] != "{{" :
                    """
                     예외처리 
                     1) { }가 없어서 infobox_content_list가 []인 경우.
                     2) {{으로 시작하지 않는 경우.
                         -> {| style="float: right; margin-left: 와 같이 스크립트가 나오는 경우
                         -> "{신라시대는 ~" 와 같이 {가 들어간 본문이 나오는 경우.
                     """
                    if len(infobox_sentence_list) == 0:
                        no_bracket_info +=1
                        continue
                    elif infobox_sentence_list[0][:2] != "{{":
                        if len(infobox_list) == 1:
                            script_page += 1
                            continue
                        else:
                            print("Infobox exists with script info in ", page_title)
                    continue

                # 찾은 Infobox를 dict형으로 반환
                # 만약 제외할 대상들은 infobox값이 None으로 리턴
                infobox = self._get_infobox2dict(infobox_sentence_list = infobox_sentence_list, page_title=page_title)
                if infobox == None:
                    continue

                infoboxes.append(infobox)

        print("finish")
        print("infobox = " , len(infoboxes))
        print("no infobox ({}로 시작하지 않음) = ", no_info)
        print("no infobox ({}로 시작하지만, info는 없음)", no_bracket_info)
        print("틀 페이지 = ", frame_info)
        print("스크립트만 있는 페이지 = ", script_page)

        return infoboxes

if __name__ == "__main__":

    extractor = InfoboxExtractor()
    parsed_data = extractor.run(input_file_name='../resources/kowiki-20200720-pages-articles-multistream.xml')

    save_file(parsed_data=parsed_data, output_file_name='../resources/full_output.json')
