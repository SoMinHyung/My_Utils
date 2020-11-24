from make_json2triple.json2triple import Infobox2Triple
from make_json2triple.trimTriple import TrimTriple


if __name__ == "__main__":
    #info박스에서 모든 개체들을 다 뽑아옴.
    info2triple = Infobox2Triple()
    info2triple.run(input_json_path = "resources/full_output.json", output_tsv_path = 'resources/full_output.tsv', isShuffle=False)

    #full_output에서 원하는 기준에 맞게 다시 뽑아줌.
    trim = TrimTriple()
    trim.run(input_tsv_path="resources/full_output.tsv", output_tsv_path="resources/full_output_trim.tsv", isShuffle=False)

    print("hello")