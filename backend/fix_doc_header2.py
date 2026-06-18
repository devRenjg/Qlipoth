# -*- coding: utf-8 -*-
"""头部再优化：①来源/父文档之间空一行 ②链接显示为"文档名(可点击)"而非裸链接。
markdown渲染支持[名](链)。来源名用文档自身标题，父文档名用父标题。只改头部。"""
import sys, io, os, re, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

changed = 0
for f in glob.glob('knowledge_base/*.md'):
    t = open(f, encoding='utf-8', errors='replace').read()
    orig = t
    # 文档标题：第一行 # xxx
    mt = re.search(r'^#\s+(.+)$', t, re.M)
    doc_title = mt.group(1).strip() if mt else os.path.basename(f)[:-3]

    # 来源行：> 来源: <url 或 已是[..](..)>  → 改成 [doc_title](url)
    def _src(m):
        rest = m.group(2).strip()
        url = rest
        mm = re.search(r'\((https?://\S+?)\)', rest)  # 已是markdown链接则取url
        if mm: url = mm.group(1)
        else:
            um = re.search(r'(https?://\S+)', rest)
            url = um.group(1) if um else rest
        url = url.split('?')[0]
        return f"> 来源: [{doc_title}]({url})"
    t = re.sub(r'^(>\s*来源[:：]\s*)(.+)$', _src, t, flags=re.M, count=1)

    # 父文档行：> 父文档: 名（url） 或 名(已有链接) → [名](url)，并在其前补空行
    def _parent(m):
        rest = m.group(2).strip()
        # 提取名与url
        url = None
        mm = re.search(r'[（(](https?://\S+?)[）)]', rest)
        if mm:
            url = mm.group(1).split('?')[0]
            name = rest[:mm.start()].strip()
        else:
            ml = re.search(r'\[([^\]]+)\]\((https?://\S+?)\)', rest)
            if ml:
                name, url = ml.group(1), ml.group(2).split('?')[0]
            else:
                name = rest
        if url:
            return f"\n> 父文档: [{name}]({url})"
        return f"\n> 父文档: {name}"
    t = re.sub(r'^(>\s*父文档[:：]\s*)(.+)$', _parent, t, flags=re.M, count=1)

    if t != orig:
        open(f, 'w', encoding='utf-8').write(t)
        changed += 1
print(f'头部再优化：修改 {changed} 篇')
