# -*- coding: utf-8 -*-
"""删除每篇文档第一行的大标题(# xxx)。文档头部第一行是文件名标题，与正文内的标题重复，
去掉后头部从"> 来源"开始。只删开头第一个 # 标题行。"""
import sys, io, os, re, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

changed = 0
for f in glob.glob('knowledge_base/*.md'):
    t = open(f, encoding='utf-8', errors='replace').read()
    # 只处理开头就是 # 标题 的（避免误删正文标题）
    m = re.match(r'^#\s+[^\n]*\n+', t)
    if not m:
        continue
    new_t = t[m.end():]   # 去掉首行标题及其后空行
    new_t = new_t.lstrip('\n')
    if new_t != t:
        open(f, 'w', encoding='utf-8').write(new_t)
        changed += 1
print(f'删除首行标题：修改 {changed} 篇')
