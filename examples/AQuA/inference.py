import pickle
from typing import Type, Callable, Optional

import numpy as np
from datasets import load_dataset
from tqdm import tqdm
from datetime import datetime

from reasoners import LanguageModel, Reasoner, SearchAlgorithm
from reasoners.algorithm import MCTS

from world_model import MATHWorldModel
from search_config import MATHConfig
import utils
import re
from datasets import Dataset

def data_reader(dataset,dataset_path, split=None, sample_size=100):
    questions = []
    answers = []
    options = []
    filename = os.path.join(dataset_path, 'AQuA.json')
    with open(filename, 'r') as file:
        lines = file.readlines()
        if split is not None:
            start, end = split
            lines = lines[start:end]
        for line in lines:
            data = json.loads(line)
            if isinstance(data, dict):
                options_list = data['options']
                options_dict = {}
                for option in options_list:
                    match = re.search(r'([A-E])\)[^0-9]*([\d.]+)', option)
                    if match:
                        options_dict[match.group(1)] = float(match.group(2))
                question_with_options = data['question'] + "\n" + "\n".join(data['options'])
                questions.append(question_with_options)
                # answers.append(options_dict.get(data['correct']))
                answers.append(data['correct'])
                options.append(options_list)
            else:
                raise ValueError("Unexpected data format")
    return Dataset.from_dict({"question": questions, "answer": answers, "options":options})


def rap_MATH(base_model: LanguageModel,
              interactive_prompt: dict,
              useful_prompt: dict,
              search_algo: Type[SearchAlgorithm] = MCTS,
              resume: int = 0,
              n_action: int = 4,
              n_confidence: int = 8,
              depth_limit: int = 5,
              force_terminating_on_depth_limit: bool = True,
              batch_size: int = 2,
              temperature: float = 0.8,
              early_stop_base: int = 2,
              early_stop_threshold: float = 0.5,
              reward_alpha: float = 0.5,
              reward_confidence_default: float = 0.8,
              cum_reward: Callable[[list[float]], float] = np.mean,
              calc_q: Callable[[list[float]], float] = max,
              log_dir: Optional[str] = None,
              datasetname: str = 'AQuA',
              dataset_path: str = r"/data/yueshan/llm-reasoners/examples/AQuA/dataset/AQuA",
              disable_log: bool = False,
              disable_tqdm: bool = False,
              **search_algo_params):
    if not disable_log:
        if log_dir is None:
            log_dir = f'logs/gsm8k_{search_algo.__name__}/{datetime.now().strftime("%m%d%Y-%H%M%S")}'
        os.makedirs(log_dir, exist_ok=resume > 0)
        os.makedirs(os.path.join(log_dir, 'algo_output'), exist_ok=True)
        with open(os.path.join(log_dir, 'args.txt'), 'w') as f:
            print(sys.argv, file=f)

    search_algo_params |= {'cum_reward': cum_reward, 'calc_q': calc_q, 'disable_tqdm': disable_tqdm}
    world_model = MATHWorldModel(base_model=base_model, prompt=interactive_prompt,
                                  n_confidence=n_confidence, batch_size=batch_size, temperature=temperature,
                                  early_stop_base=early_stop_base, early_stop_threshold=early_stop_threshold)
    config = MATHConfig(base_model=base_model, prompt=interactive_prompt, useful_prompt=useful_prompt,
                         n_actions=n_action, batch_size=batch_size, temperature=temperature,
                         reward_alpha=reward_alpha, reward_confidence_default=reward_confidence_default,
                         force_terminating_on_depth_limit=force_terminating_on_depth_limit, depth_limit=depth_limit)
    search_algo = search_algo(**search_algo_params)
    agent = Reasoner(world_model=world_model, search_config=config, search_algo=search_algo)
    dataset = data_reader(datasetname, dataset_path)
    correct_count = 0
    for i, example in enumerate(tqdm(dataset, total=resume + len(dataset), initial=resume,
                                     desc='GSM8k', disable=disable_tqdm)):
        # algo_output = agent(example["question"])
        # if algo_output and algo_output.terminal_state and algo_output.terminal_state[-1] and algo_output.terminal_state[-1].sub_answer:
        #     output = utils.retrieve_answer(algo_output.terminal_state[-1].sub_answer)
        #     output_all = algo_output.terminal_state[-1].sub_answer
        # else:
        #     output = "Didn't get any output"
        #     output_all = "Didn't get any output"
        max_attempts = 3
        for attempt in range(max_attempts):
            algo_output = agent(example["question"])
            if algo_output and algo_output.terminal_state and algo_output.terminal_state[-1] and algo_output.terminal_state[-1].sub_answer:
                output = utils.retrieve_answer(algo_output.terminal_state[-1].sub_answer)
                output_all = algo_output.terminal_state[-1].sub_answer
                break 
            else: 
                output = "Didn't get any output"
                output_all = "Didn't get any output"
        
        answer = (example["answer"])
        question = (example["question"])
        correct = utils.judge_answer(output, answer)
        correct_count += correct
        accuracy = correct_count / (i + 1)
        # log_str = f'Case #{resume + i + 1}: {correct=}, {question=}, {output_all=}, {output=}, {answer=}; {accuracy=:.3f} ({correct_count}/{i + 1})'
        log_str = f'Case #{resume + i + 1}: /n{correct=}, /n{question=}, /n{output_all=}, /n{output=}, /n{answer=}; /n{accuracy=:.3f} ({correct_count}/{i + 1})'
        tqdm.write(log_str)
        if not disable_log:
            with open(os.path.join(log_dir, 'result.log'), 'a') as f:
                print(log_str, file=f)
            with open(os.path.join(log_dir, 'algo_output', f'{resume + i + 1}.pkl'), 'wb') as f:
                pickle.dump(algo_output, f)


if __name__ == '__main__':
    import os
    import sys
    import json
    import warnings
    import fire
    from reasoners.lm import LLaMAModel, LlamaCppModel
    import random
    import torch
    import torch.backends.cudnn

    np.random.seed(0)
    random.seed(0)
    torch.manual_seed(0)
    torch.cuda.manual_seed(0)
    torch.backends.cudnn.deterministic = True

    llama_ckpts = os.environ.get("LLAMA_CKPTS", None)
    local_rank = int(os.environ.get("LOCAL_RANK", 0))
    if local_rank != 0:
        sys.stdout = open(os.devnull, 'w')
        warnings.filterwarnings('ignore')


    def main(base_lm: str = 'llama',
             llama_ckpt: str = llama_ckpts,
             llama_size: str = '13B',
             llama_cpp_path: str = None,
             batch_size: int = 2,
             interactive_prompt: str = '/data/yueshan/llm-reasoners/examples/AQuA/prompts/interactive_examples.json',
             useful_prompt: str = '/data/yueshan/llm-reasoners/examples/AQuA/prompts/useful_examples.json',
             disable_log: bool = False,
             disable_tqdm: bool = False,
             **kwargs):
        # set base_lm = 'llama' and llama_ckpt = '13B/30B/65B' to use llama with torchscale
        # else set base_lm = 'llama.cpp' and llama_cpp_path the the checkpoint to use llama.cpp

        with open(interactive_prompt) as f:
            interactive_prompt = json.load(f)
        with open(useful_prompt) as f:
            useful_prompt = json.load(f)
        if base_lm == 'llama':
            base_model = LLaMAModel(llama_ckpt, llama_size, max_batch_size=batch_size)
        elif base_lm == 'llama.cpp':
            base_model = LlamaCppModel(llama_cpp_path)
        else:
            base_model = None
            assert False, f'cannot resolve {base_lm=}'
        rap_MATH(base_model=base_model,
                  interactive_prompt=interactive_prompt,
                  useful_prompt=useful_prompt,
                  batch_size=batch_size,
                  disable_log=disable_log or local_rank != 0,
                  disable_tqdm=disable_tqdm or local_rank != 0,
                  **kwargs)


    fire.Fire(main)
