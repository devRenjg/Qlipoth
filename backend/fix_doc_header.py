# -*- coding: utf-8 -*-
"""优化文档头部：来源/父文档各一行清晰；父文档补上链接(从import_trees取)；
URL去掉超长的?scode=查询尾巴更清爽。只改头部，不动正文。"""
import sys, io, os, re, glob, json, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from database import DB_PATH

# 建 父文档title -> url 映射
db = sqlite3.connect(DB_PATH); db.row_factory = sqlite3.Row
t2u = {}
for r in db.execute('SELECT tree_data FROM import_trees').fetchall():
    for n in json.loads(r['tree_data']):
        if n.get('title') and n.get('url'):
            t2u[n['title'].strip()] = n['url'].split('?')[0]  # 去查询参数
db.close()

def short(u):
    return u.split('?')[0]  # 去掉 ?scode=... 尾巴

changed = 0
for f in glob.glob('knowledge_base/*.md'):
    t = open(f, encoding='utf-8', errors='replace').read()
    orig = t
    # 来源URL去查询参数
    t = re.sub(r'^(>\s*来源[:：]\s*)(\S+)$',
               lambda m: m.group(1) + short(m.group(2)), t, flags=re.M)
    # 父文档行补链接
    def _parent(m):
        pt = m.group(2).strip()
        url = t2u.get(pt)
        return f"{m.group(1)}{pt}（{url}）" if url else f"{m.group(1)}{pt}"
    t = re.sub(r'^(>\s*父文档[:：]\s*)(.+?)(（http\S+）)?$', _parent, t, flags=re.M)
    if t != orig:
        open(f, 'w', encoding='utf-8').write(t)
        changed += 1
print(f'优化头部：修改 {changed} 篇')
