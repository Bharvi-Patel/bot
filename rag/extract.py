import re, json, sys
import sys, os
SRC = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('SJ_DUMP', 'southjacksonfurniture_dev.sql')   # path to the .sql dump
WANT={'pages','page_languages','faqs','faq_languages','blogs','blog_languages','languages','product_faqs','category_faqs','blog_categories'}
def parse_values(s):
    rows=[];i=0;n=len(s)
    while i<n:
        if s[i]=='(':
            i+=1;row=[]
            while True:
                c=s[i]
                if c=="'":
                    i+=1;buf=[]
                    while True:
                        c=s[i]
                        if c=='\\':
                            nx=s[i+1]
                            buf.append({'n':'\n','r':'\r','t':'\t','0':'\0','Z':'\x1a'}.get(nx,nx));i+=2
                        elif c=="'":
                            if s[i+1]=="'": buf.append("'");i+=2
                            else: i+=1;break
                        else: buf.append(c);i+=1
                    row.append(''.join(buf))
                else:
                    j=i
                    while s[j] not in ',)': j+=1
                    tok=s[i:j].strip();i=j
                    row.append(None if tok=='NULL' else tok)
                if s[i]==',': i+=1;continue
                if s[i]==')': i+=1;break
            rows.append(row)
        else: i+=1
    return rows
cols={};out={}
with open(SRC,'r',encoding='utf8',errors='replace',newline='') as f:
    cur=None
    for line in f:
        m=re.match(r'CREATE TABLE `(\w+)`',line)
        if m: cur=m.group(1); cols[cur]=[]; continue
        if cur and line.startswith('  `'):
            cols[cur].append(re.match(r'  `(\w+)`',line).group(1))
        if line.startswith(') ENGINE'): cur=None
        m=re.match(r'INSERT INTO `(\w+)` VALUES (.*);\s*$',line,re.S)
        if m and m.group(1) in WANT:
            t=m.group(1)
            out.setdefault(t,[]).extend(dict(zip(cols[t],r)) for r in parse_values(m.group(2)))
json.dump(out,open('raw.json','w'))
for t,r in out.items(): print(t,len(r))