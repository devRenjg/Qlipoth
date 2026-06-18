# -*- coding: utf-8 -*-
"""对"首行标题与正文第一个标题完全相同"的企微文档，删掉正文里那个重复标题(保留首行)。
只处理完全相同的，相似/不同/单标题的都不动。"""
import sys, io, os, re, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from database import DB_PATH

db = sqlite3.connect(DB_PATH); db.row_factory = sqlite3.Row
rows = db.execute("SELECT stored_path FROM documents WHERE source_url LIKE '%doc.weixin%'").fetchall()
db.close()

changed = 0
for r in rows:
    p = 'knowledge_base/' + r['stored_path']
    if not os.path.exists(p): continue
    t = open(p, encoding='utf-8', errors='replace').read()
    m1 = re.match(r'^#\s+(.+)', t)
    if not m1: continue
    h1 = m1.group(1).strip()
    # 定位头部之后正文的第一个标题行
    # 头部 = 首行# + 紧随的 > 行 和空行
    rest = t[m1.end():]
    # 匹配:换行们 + 可选的(> 来源/父文档/子文档 行 + 空行) + 然后第一个 #+ 标题
    m2 = re.match(r'((?:\n|\r)+(?:>[^\n]*(?:\n|\r)+)*)(#+\s+([^\n]+))', rest)
    if not m2: continue
    h2 = m2.group(3).strip()
    if h1 != h2:   # 只处理完全相同
        continue
    # 删掉正文里那个重复标题行(m2.group(2)),保留前面的头部块
    new_rest = rest[:m2.start(2)] + rest[m2.end(2):]
    new_rest = re.sub(r'^(\s*\n)+', '\n', new_rest)
    new_t = t[:m1.end()] + new_rest
    open(p, 'w', encoding='utf-8').write(new_t)
    changed += 1
print(f'删除重复正文标题(完全相同)：处理 {changed} 篇')
