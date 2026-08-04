with open('app/ai/agents/__init__.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i in range(len(lines)):
    if 'prompt_vlm = \"\"\"' in lines[i]:
        # fix indentation
        lines[i] = lines[i].replace('                prompt_vlm = ', '        prompt_vlm = ')

with open('app/ai/agents/__init__.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)
