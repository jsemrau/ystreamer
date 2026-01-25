import os

# Environment configuration
os.environ['HF_HOME'] = "/home/moebius/Models/hfmodels/.cache/"
os.environ['PYTORCH_CUDA_ALLOC_CONF'] = "expandable_segments:True"
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"] = "0,1"

from smolagents import CodeAgent, TransformersModel, DuckDuckGoSearchTool
import torch
from transformers import BitsAndBytesConfig

# Configure 4-bit quantization (FP8 requires compute capability 8.9+)
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_compute_dtype=torch.float16,
    llm_int8_enable_fp32_cpu_offload=True,
)

# 14B model with 4-bit quantization should fit in 20GB total VRAM
model = TransformersModel(
    model_id="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",
    device_map="auto",           
    dtype=torch.float16,   
    trust_remote_code=True,
    model_kwargs={
        "quantization_config": bnb_config,    
    }
)

agent = CodeAgent(tools=[DuckDuckGoSearchTool()], model=model)

agent.run("Write a Python script to check if my RTX 3060 and RTX 2080S are both visible to PyTorch.")