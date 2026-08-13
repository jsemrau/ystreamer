from transformers import AutoModelForCausalLM, AutoTokenizer
import os, sys, re
import torch

os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:32"
os.environ['CUDA_DEVICE_ORDER'] = 'PCI_BUS_ID'
os.environ['CUDA_VISIBLE_DEVICES'] = '0,1'
os.environ["PYTORCH_USE_CUDA_DSA"] = "0"
os.environ['HF_HOME']='/home/moebius/Models/hfmodels/.cache/'


model_name = "Qwen/Qwen-AgentWorld-35B-A3B"
tokenizer = AutoTokenizer.from_pretrained(model_name)

model = AutoModelForCausalLM.from_pretrained(
    model_name,
    torch_dtype="auto",
    device_map="auto",
    max_memory={0: "6GiB", 1: "9GiB", "cpu": "40GiB"},
    offload_folder="/home/moebius/Models/hfmodels/.cache/offload_qwentest",
)

messages = [
    {
        "role": "system",
        "content": "You are a language world model simulating a Linux terminal environment. "
                   "Given the user's command, predict the terminal output."
    },
    {
        "role": "user",
        "content": "Action: execute_bash\nCommand: ls -la /home/user/project/"
    }
]

text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
inputs = tokenizer([text], return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=2048, temperature=0.6)
response = tokenizer.decode(outputs[0][inputs.input_ids.shape[-1]:], skip_special_tokens=True)
print(response)
