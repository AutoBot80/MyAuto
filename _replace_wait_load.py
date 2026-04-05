import re

path = 'backend/app/services/fill_hero_insurance_service.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Pattern: try:\n    INDENT page.wait_for_load_state("domcontentloaded", timeout=EXPR)\n    INDENT except Exception:\n    INDENT pass
# The timeout expression can span multiple lines (like in line 7085-7087 and 8688-8690)
# Let's handle single-line and multi-line timeout separately

# Single-line pattern
pattern1 = re.compile(
    r'(\s+)try:\n'
    r'\1    page\.wait_for_load_state\("domcontentloaded", timeout=(.*?)\)\n'
    r'\1except Exception:\n'
    r'\1    pass',
    re.DOTALL
)

count = 0
def replace1(m):
    global count
    indent = m.group(1)
    timeout = m.group(2)
    count += 1
    return f'{indent}_wait_load_optional(page, {timeout})'

content_new = pattern1.sub(replace1, content)
print(f'Single-line replacements: {count}')

# Multi-line timeout patterns (where timeout= spans to next line)
pattern2 = re.compile(
    r'(\s+)try:\n'
    r'\1    page\.wait_for_load_state\(\n'
    r'\1        "domcontentloaded", timeout=(.*?)\n'
    r'\1    \)\n'
    r'\1except Exception:\n'
    r'\1    pass',
    re.DOTALL
)

count2 = 0
def replace2(m):
    global count2
    indent = m.group(1)
    timeout = m.group(2).rstrip(',')
    count2 += 1
    return f'{indent}_wait_load_optional(page, {timeout})'

content_new = pattern2.sub(replace2, content_new)
print(f'Multi-line replacements: {count2}')
print(f'Total: {count + count2}')

with open(path, 'w', encoding='utf-8') as f:
    f.write(content_new)

# Count remaining unreplaced patterns
remaining = content_new.count('wait_for_load_state("domcontentloaded"')
remaining += content_new.count("wait_for_load_state('domcontentloaded'")
print(f'Remaining wait_for_load_state(domcontentloaded) calls: {remaining}')
