from openai import OpenAI
API_KEY = "sk-99f0c3fc32a4413cbf01a4ab90e2ee6a"
import openai.types.chat.chat_completion as Message
Message = Message.Choice
class Deepseek:
    def __init__(self,api_key:str,model:str):
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com"
        )
        self.model = model

    def add_user_message(self, messages: list, message):
        """Add user message to the conversation history."""
        # 如果是工具结果列表
        if isinstance(message, list):
            # 直接扩展消息列表
            messages.extend(message)
        else:
            # 普通用户消息
            user_message = {
                "role": "user",
                "content": message.message.content if hasattr(message, 'message') and hasattr(message.message, 'content') else str(message)
            }
            messages.append(user_message)

    def add_assistant_message(self, messages: list, message):
        """Add assistant message to the conversation history."""
        if hasattr(message, 'message'):
            assistant_message = {
                "role": "assistant",
                "content": message.message.content
            }
            
            # 如果有 tool_calls，添加到消息中
            if hasattr(message.message, 'tool_calls') and message.message.tool_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    }
                    for tc in message.message.tool_calls
                ]
        else:
            assistant_message = {
                "role": "assistant",
                "content": str(message)
            }
        
        messages.append(assistant_message)

    def text_from_message(self,message):
        return message.message.content
    
    def chat(
        self,
        messages,
        system = None,
        temperature = 1.0,
        tools = None    
    ):
        params = {
            "model":self.model,
            "messages":messages,
            "temperature":temperature
        }

        if system:
            params['system'] = system

        if tools:
            params['tools'] = tools

        response = self.client.chat.completions.create(**params)
        return response.choices[0]