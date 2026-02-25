import os
from openai import AzureOpenAI

client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_KEY"),
    api_version="2025-04-01-preview",
    azure_endpoint="https://fullstackdevclinigma.openai.azure.com"
)
