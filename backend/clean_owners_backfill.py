# -*- coding: utf-8 -*-
"""清洗已导文档头部的"> 负责人:"行：用新过滤规则去掉噪声ID/正文误抓，只留真实人名。
只改头部负责人行，不动正文。负责人全被过滤掉则删除该行。"""
import sys, io, os, re, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(__file__))
from parsers import _looks_like_noise_id, _NON_NAME_HINT

def clean_name(n):
    n = n.strip().lstrip('@').strip()
    if not n: return None
    if _looks_like_noise_id(n) or _NON_NAME_HINT.search(n): return None
    return n

changed = 0; total_doc = 0; removed_names = 0; kept_names = 0
for f in glob.glob('knowledge_base/*.md'):
    t = open(f, encoding='utf-8', errors='replace').read()
    m = re.search(r'^(>\s*负责人[:：]\s*)(.+)$', t, re.M)
    if not m: continue
    total_doc += 1
    raw = [x for x in m.group(2).split(',')]
    before = len([x for x in raw if x.strip()])
    cleaned = []
    seen = set()
    for x in raw:
        c = clean_name(x)
        if c and c not in seen:
            seen.add(c); cleaned.append(c)
    kept_names += len(cleaned); removed_names += before - len(cleaned)
    if len(cleaned) == before and all(raw[i].strip().lstrip('@').strip()==cleaned[i] for i in range(len(cleaned)) if i<len(cleaned)):
        # 看是否真有变化
        pass
    new_line = (m.group(1) + ', '.join('@'+n for n in cleaned)) if cleaned else None
    old_line = m.group(0)
    if new_line == old_line: continue
    if new_line is None:
        # 负责人全是噪声→删整行(连同行尾换行)
        new_t = t[:m.start()] + t[m.end():]
        new_t = re.sub(r'\n\n\n+', '\n\n', new_t)
    else:
        new_t = t[:m.start()] + new_line + t[m.end():]
    if new_t != t:
        open(f, 'w', encoding='utf-8').write(new_t)
        changed += 1

print(f'扫描含负责人行文档 {total_doc} 篇，清洗修改 {changed} 篇')
print(f'负责人条目：保留 {kept_names} 个，去除噪声 {removed_names} 个')
