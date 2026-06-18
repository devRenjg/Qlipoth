# -*- coding: utf-8 -*-
"""删除所有已导文档头部的"> 负责人:"行(创建人拿不到、@提及负责人不准确)。
只删负责人行，保留来源链接、父/子文档等其他头部信息。"""
import sys, io, os, re, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

changed = 0
for f in glob.glob('knowledge_base/*.md'):
    t = open(f, encoding='utf-8', errors='replace').read()
    # 删除整行 "> 负责人: ..."(含行尾换行)
    new_t = re.sub(r'^>\s*负责人[:：].*\n', '', t, flags=re.M)
    if new_t != t:
        open(f, 'w', encoding='utf-8').write(new_t)
        changed += 1
print(f'删除负责人行：修改 {changed} 篇文档')
# 残留检查
left = sum(1 for f in glob.glob('knowledge_base/*.md')
           if re.search(r'^>\s*负责人', open(f, encoding='utf-8', errors='replace').read(), re.M))
print(f'仍含负责人行的文档：{left} 篇')
