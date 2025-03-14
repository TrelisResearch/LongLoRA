import os
import sys
import math
import torch
import argparse
import textwrap
import transformers
from peft import PeftModel
from transformers import GenerationConfig
from llama_attn_replace import replace_llama_attn
import gradio as gr


def parse_config():
    parser = argparse.ArgumentParser(description='arg parser')
    parser.add_argument('--base_model', type=str, default="/data1/pretrained-models/llama-7b-hf")
    parser.add_argument('--cache_dir', type=str, default="./cache")
    parser.add_argument('--context_size', type=int, default=-1, help='context size during fine-tuning')
    parser.add_argument('--flash_attn', type=bool, default=True, help='')
    parser.add_argument('--temperature', type=float, default=0.6, help='')
    parser.add_argument('--top_p', type=float, default=0.9, help='')
    parser.add_argument('--max_gen_len', type=int, default=512, help='')
    args = parser.parse_args()
    return args

title = "LongLoRA and LongAlpaca for Long-context LLMs"

description = """
<font size=4>
This is the online demo of LongLoRA. \n
If multiple users are using it at the same time, they will enter a queue, which may delay some time. \n
**Inputs**: <br>
- **Input material txt** and **Question** are required. <br>
**Note**: <br>
- The demo model is **LongAlpaca-7B**. We use 4-bit quantization for low GPU memory inference, which may impair text-generation quality.<br> 
- There are 10 book-related examples and 5 paper-related examples, 15 in total.<br>
- Note that only txt file is currently support.\n
**Example questions**: <br>
&ensp; Please summarize the book in one paragraph. <br>
&ensp; Please tell me that what high-level idea the author want to indicate in this book. <br>
&ensp; Please describe the relationship among the roles in the book. <br>
&ensp; Please summarize the paper in one paragraph. <br>
&ensp; What is the main contribution of this paper? <br>
Hope you can enjoy our work!
</font>
"""

# Gradio
examples = [
    [
        "./materials/The Three-Body Problem_section3.txt",
        "Please describe the relationship among the roles in the book.",
    ],
    [
        "./materials/Death’s End_section9.txt",
        "Please tell me that what high-level idea the author want to indicate in this book.",
    ],
    [
        "./materials/Death’s End_section12.txt",
        "What responsibility do we as individuals have to make moral choices that benefit the greater good of humanity and the universe?",
    ],

    [
        "./materials/Journey to the West_section13.txt",
        "How does Monkey's character change over the course of the journey?",
    ],
    [
        "./materials/Journey to the West_section33.txt",
        "Please tell me that what high-level idea the author want to indicate in this book.",
    ],
    [
        "./materials/Dream of the Red Chamber_section17.txt",
        "Please tell me that what high-level idea the author want to indicate in this book.",
    ],

    [
        "./materials/Harry Potter and the Philosophers Stone_section2.txt",
        "Why doesn't Professor Snape seem to like Harry?",
    ],
    [
        "./materials/Harry Potter The Chamber of Secrets_section2.txt",
        "Please describe the relationship among the roles in the book.",
    ],

    [
        "./materials/Don Quixote_section3.txt",
        "What theme does Don Quixote represent in the story?",
    ],

    [
        "./materials/The Lord Of The Rings 2 - The Two Towers_section3.txt",
        "What does this passage reveal about Gandalf's character and role?",
    ],

    [
        "./materials/paper_1.txt",
        "What are the main contributions and novelties of this work?",
    ],
    [
        "./materials/paper_2.txt",
        "Please summarize the paper in one paragraph.",
    ],
    [
        "./materials/paper_3.txt",
        "What are some limitations of the proposed 3DGNN method?",
    ],
    [
        "./materials/paper_4.txt",
        "What is the main advantage of the authors' energy optimization based texture design method compared to other existing texture synthesis techniques?",
    ],
    [
        "./materials/paper_5.txt",
        "What are some best practices for effectively eliciting software requirements?",
    ],
]

article = """
<p style='text-align: center'>
<a href='https://arxiv.org/abs/2308.00692' target='_blank'>
Preprint Paper
</a>
\n
<p style='text-align: center'>
<a href='https://github.com/dvlab-research/LongLoRA' target='_blank'>   Github Repo </a></p>
"""

PROMPT_DICT = {
    "prompt_no_input": (
        "Below is an instruction that describes a task. "
        "Write a response that appropriately completes the request.\n\n"
        "### Instruction:\n{instruction}\n\n### Response:"
    ),
}


def read_txt_file(material_txt):
    content = ""
    with open(material_txt) as f:
        for line in f.readlines():
            content += line
    return content

def build_generator(
    model, tokenizer, temperature=0.6, top_p=0.9, max_gen_len=4096, use_cache=True
):
    def response(material, question):
        if material is None:
            return "Only support txt file."

        if not material.name.split(".")[-1]=='txt':
            return "Only support txt file."

        material = read_txt_file(material.name)
        prompt_no_input = PROMPT_DICT["prompt_no_input"]
        prompt = prompt_no_input.format_map({"instruction": material + "\n%s" % question})

        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

        if len(inputs['input_ids'][0]) > 32768:
            return "This demo supports tokens less than 32768, while the current is %d. Please use material with less tokens."%len(inputs['input_ids'][0])
        torch.cuda.empty_cache()

        output = model.generate(
            **inputs,
            max_new_tokens=max_gen_len,
            temperature=temperature,
            top_p=top_p,
            use_cache=use_cache
        )
        out = tokenizer.decode(output[0], skip_special_tokens=True)

        out = out.split(prompt)[1].strip()
        print("out", out)
        return out

    return response

def main(args):
    if args.flash_attn:
        replace_llama_attn(inference=True)

    # Set RoPE scaling factor
    config = transformers.AutoConfig.from_pretrained(
        args.base_model,
        cache_dir=args.cache_dir,
    )

    orig_ctx_len = getattr(config, "max_position_embeddings", None)
    if orig_ctx_len and args.context_size > orig_ctx_len:
        scaling_factor = float(math.ceil(args.context_size / orig_ctx_len))
        config.rope_scaling = {"type": "linear", "factor": scaling_factor}

    # Load model and tokenizer
    model = transformers.AutoModelForCausalLM.from_pretrained(
        args.base_model,
        config=config,
        cache_dir=args.cache_dir,
        torch_dtype=torch.float16,
        load_in_4bit=True,
        device_map="auto",
    )
    model.resize_token_embeddings(32001)

    tokenizer = transformers.AutoTokenizer.from_pretrained(
        args.base_model,
        cache_dir=args.cache_dir,
        model_max_length=args.context_size if args.context_size > orig_ctx_len else orig_ctx_len,
        padding_side="right",
        use_fast=False,
    )

    model.eval()
    if torch.__version__ >= "2" and sys.platform != "win32":
        model = torch.compile(model)
    respond = build_generator(model, tokenizer, temperature=args.temperature, top_p=args.top_p,
                              max_gen_len=args.max_gen_len, use_cache=True)

    demo = gr.Interface(
        respond,
        inputs=[
            gr.File(type="file", label="Input material txt"),
            gr.Textbox(lines=1, placeholder=None, label="Question"),
        ],
        outputs=[
            gr.Textbox(lines=1, placeholder=None, label="Text Output"),
        ],
        title=title,
        description=description,
        article=article,
        examples=examples,
        allow_flagging="auto",
    )

    demo.queue()
    demo.launch(show_error=True, share=True)

if __name__ == "__main__":
    args = parse_config()
    main(args)
