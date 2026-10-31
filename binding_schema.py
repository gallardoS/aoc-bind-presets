"""Discover and edit binding strings in RON without evaluating configuration text."""
from dataclasses import dataclass, field
import json
import re


@dataclass
class Token:
    kind: str
    text: str
    start: int
    end: int


def tokens(text):
    result=[]; i=0
    while i<len(text):
        if text[i].isspace(): i+=1; continue
        if text.startswith('//',i):
            end=text.find('\n',i); i=len(text) if end<0 else end+1; continue
        if text.startswith('/*',i):
            depth=1; i+=2
            while i<len(text) and depth:
                if text.startswith('/*',i): depth+=1;i+=2
                elif text.startswith('*/',i): depth-=1;i+=2
                else:i+=1
            if depth:raise ValueError('Unclosed configuration comment.')
            continue
        raw=re.match(r'r(#{0,16})"',text[i:])
        if raw:
            marker='"'+raw.group(1);end=text.find(marker,i+raw.end())
            if end<0:raise ValueError('Unclosed configuration string.')
            stop=end+len(marker);result.append(Token('raw',text[i:stop],i,stop));i=stop;continue
        if text[i]=='"':
            start=i;i+=1
            while i<len(text):
                if text[i]=='\\':i+=2
                elif text[i]=='"':i+=1;break
                else:i+=1
            else:raise ValueError('Unclosed configuration string.')
            result.append(Token('string',text[start:i],start,i));continue
        if text[i] in '()[]{}:,':
            result.append(Token(text[i],text[i],i,i+1));i+=1;continue
        match=re.match(r'[^\s()\[\]{}:,"/]+',text[i:])
        if not match:raise ValueError('Unsupported configuration syntax.')
        stop=i+match.end();result.append(Token('atom',text[i:stop],i,stop));i=stop
    return result


def string_value(token):
    if token.kind=='raw':
        match=re.match(r'r(#{0,16})"',token.text)
        return token.text[match.end():-(len(match.group(1))+1)]
    return json.loads(token.text)


@dataclass
class Node:
    kind: str
    start: int
    end: int
    children: dict=field(default_factory=dict)
    names: dict=field(default_factory=dict)
    value: str|None=None
    close: int=0
    comma: bool=False
    opener: str=''


class Parser:
    def __init__(self,text):self.text=text;self.items=tokens(text)

    def parse(self,index,depth=0,record=False):
        if depth>64:raise ValueError('The configuration is nested too deeply.')
        if index>=len(self.items):raise ValueError('Incomplete configuration.')
        token=self.items[index]
        if token.kind in ('string','raw'):
            return Node('string',token.start,token.end,value=string_value(token)),index+1
        if token.kind not in ('(','[','{'):
            if index+1<len(self.items) and self.items[index+1].kind=='(':
                node,end=self.parse(index+1,depth+1)
                node.start=token.start;node.opener=token.text+'(';return node,end
            return Node('atom',token.start,token.end),index+1
        closing={'(':')','[':']','{':'}'}[token.kind]
        i=index+1
        is_record=record or token.kind=='{' or (i+1<len(self.items) and self.items[i+1].kind==':')
        node=Node('record' if is_record else 'sequence',token.start,0,opener=token.kind)
        while i<len(self.items) and self.items[i].kind!=closing:
            if is_record:
                name=self.items[i]
                key=string_value(name) if name.kind in ('string','raw') else name.text
                if i+1>=len(self.items) or self.items[i+1].kind!=':':raise ValueError('Unrecognized binding field.')
                if key in node.children:raise ValueError('Duplicate binding field: '+str(key))
                node.names[key]=name.text;i+=2
            else:key=len(node.children)
            child,i=self.parse(i,depth+1)
            node.children[key]=child;node.comma=False
            if i<len(self.items) and self.items[i].kind==',':node.comma=True;i+=1
            elif i>=len(self.items) or self.items[i].kind!=closing:raise ValueError('Missing comma in the configuration.')
        if i>=len(self.items):raise ValueError('Incomplete binding section.')
        node.close=self.items[i].start;node.end=self.items[i].end
        return node,i+1


def path_name(path):
    if len(path)==2 and path[0]=='hotbar' and isinstance(path[1],int):return f'action_{path[1]+1}'
    name=''
    for part in path:
        name+=f'[{part}]' if isinstance(part,int) else ('.' if name else '')+part
    return 'keys.'+name if re.fullmatch(r'action_\d+',name) else name


def label_for(key):
    if re.fullmatch(r'action_\d+',key):return 'Action '+key.split('_')[1]
    text=re.sub(r'\[(\d+)\]',lambda m:' '+str(int(m[1])+1),key)
    text=re.sub(r'(?<=[a-z0-9])(?=[A-Z])',' ',text).replace('_',' ').replace('.',' ')
    return text[:1].upper()+text[1:]


@dataclass
class Binding:
    key: str
    path: tuple
    node: Node


class Schema:
    def __init__(self,text):
        self.text=text;parser=Parser(text);items=parser.items
        stack=[]
        for token in items:
            if token.kind in ('(','[','{'):stack.append(token.kind)
            elif token.kind in (')',']','}'):
                if not stack or stack.pop()!={')':'(',']':'[','}':'{'}[token.kind]:
                    raise ValueError('Unbalanced configuration brackets.')
        if stack:raise ValueError('Incomplete configuration.')
        starts=[i+2 for i in range(len(items)-2) if items[i].kind=='atom' and items[i].text=='keys' and items[i+1].kind==':' and items[i+2].kind in ('(','{')]
        if len(starts)!=1:raise ValueError('The keys section is not recognized. No changes were made.')
        self.root,_=parser.parse(starts[0],record=True)
        self.bindings={}
        def collect(node,path):
            if node.kind=='string':
                key=path_name(path)
                if key in self.bindings:raise ValueError('Ambiguous binding field: '+key)
                self.bindings[key]=Binding(key,path,node)
            for name,child in node.children.items():collect(child,path+(name,))
        collect(self.root,())
        if not self.bindings:raise ValueError('No key bindings were found in the configuration.')

    def values(self):return {key:binding.node.value for key,binding in self.bindings.items()}

    def patch(self,updates):
        unknown=set(updates)-set(self.bindings)
        if unknown:raise ValueError('These bindings are not in this settings file: '+', '.join(sorted(unknown)))
        changes=[(self.bindings[key].node.start,self.bindings[key].node.end,json.dumps(value)) for key,value in updates.items()]
        result=self.text
        for start,end,value in sorted(changes,reverse=True):result=result[:start]+value+result[end:]
        return result


def patch_document(text,updates,template=None):
    target=Schema(text)
    if set(updates)<=set(target.bindings):return target.patch(updates)
    if template is None:return target.patch(updates)  # Clear error before changing anything.
    source=Schema(Schema(template).patch(updates))
    wanted={source.bindings[key].path for key in updates}
    def needed(path):return any(p[:len(path)]==path for p in wanted)
    def merge(dst,src,path):
        if not needed(path):return text[dst.start:dst.end]
        if path in wanted:return source.text[src.start:src.end]
        if dst.kind!=src.kind or dst.opener!=src.opener:
            raise ValueError('The account and local binding formats differ. Save the settings in the game and try again.')
        edits=[];additions=[]
        for key,child in src.children.items():
            child_path=path+(key,)
            if not needed(child_path):continue
            if key in dst.children:
                old=dst.children[key];edits.append((old.start,old.end,merge(old,child,child_path)))
            else:
                if src.kind=='sequence' and key!=len(dst.children)+len(additions):
                    raise ValueError('Cannot safely extend this binding list.')
                prefix=src.names[key]+': ' if src.kind=='record' else ''
                additions.append(prefix+source.text[child.start:child.end])
        if additions:
            prefix='' if not dst.children or dst.comma else ','
            edits.append((dst.close,dst.close,prefix+'\n    '+',\n    '.join(additions)+',\n'))
        result=text[dst.start:dst.end]
        for start,end,replacement in sorted(edits,reverse=True):
            result=result[:start-dst.start]+replacement+result[end-dst.start:]
        return result
    merged=merge(target.root,source.root,())
    return text[:target.root.start]+merged+text[target.root.end:]
