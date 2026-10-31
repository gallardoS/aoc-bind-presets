"""Classic Warcraft-style native Tk theme using the local game's menu textures."""
import tkinter as tk
from tkinter import ttk

BG = '#0d1217'
FIELD = '#211c13'
GOLD = '#ffd100'
BORDER = '#8e7135'
TEXT = '#e9e3d3'
MUTED = '#b8a780'
SELECTED = '#493a17'


def install(root, resources):
    style = ttk.Style(root)
    style.theme_use('clam')
    root.configure(bg=BG)
    images = {}
    for name,file in [('stone','stone.png'),('frame','frame.png')]:
        images[name] = tk.PhotoImage(master=root,file=str(resources/'icons'/file))
    style.element_create('WC3.stone','image',images['stone'],border=0,sticky='nsew',width=1,height=1)
    style.element_create('WC3.frame','image',images['frame'],border=30,sticky='nsew',width=60,height=60)
    style.layout('TFrame',[('WC3.stone',{'sticky':'nsew','children':[('Frame.padding',{'sticky':'nsew'})]})])
    style.layout('Window.TFrame',[
        ('WC3.stone',{'sticky':'nsew'}),
        ('WC3.frame',{'sticky':'nsew','children':[('Frame.padding',{'sticky':'nsew'})]})])
    style.layout('TLabel',[('WC3.stone',{'sticky':'nsew','children':[
        ('Label.padding',{'sticky':'nsew','children':[('Label.label',{'sticky':'nsew'})]})]})])
    style.configure('.',background=BG,foreground=TEXT,font=('Georgia',10),bordercolor=BORDER)
    style.configure('TLabel',background=BG,foreground=TEXT)
    style.configure('Title.TLabel',font=('Georgia',24,'bold'),foreground=GOLD,anchor='center')
    style.configure('Heading.TLabel',font=('Georgia',13,'bold'),foreground=GOLD)
    style.configure('Conflict.TLabel',foreground='#ff7373')
    style.configure('TSeparator',background=BORDER)
    separator = tk.PhotoImage(master=root,width=4,height=2)
    separator.put(BORDER,to=(0,0,4,1)); separator.put('#302819',to=(0,1,4,2))
    images['separator'] = separator
    style.element_create('WC3.separator','image',separator,border=0,sticky='nsew',width=1,height=2)
    style.layout('Horizontal.TSeparator',[('WC3.separator',{'sticky':'nsew'})])

    def button_image(name,fill,edge,light):
        image = tk.PhotoImage(master=root,width=40,height=28)
        image.put('#060807',to=(0,0,40,28))
        image.put(edge,to=(1,1,39,27))
        image.put(light,to=(2,2,38,3))
        image.put(fill,to=(3,3,37,25))
        image.put('#100d08',to=(3,25,38,26))
        image.put('#100d08',to=(37,3,38,26))
        images[name] = image
        return image
    normal = button_image('button',FIELD,BORDER,'#bf9c50')
    hover = button_image('hover','#3b3019','#d9b64f','#f5d873')
    pressed = button_image('pressed','#151108','#b29543','#4c3b16')
    disabled = button_image('disabled','#171714','#474337','#56513e')
    style.element_create('WC3.button','image',normal,('disabled',disabled),('pressed',pressed),('active',hover),border=5,sticky='nsew',width=10,height=10)
    style.layout('TButton',[('WC3.button',{'sticky':'nsew','children':[
        ('Button.focus',{'sticky':'nsew','children':[('Button.padding',{'sticky':'nsew','children':[('Button.label',{'sticky':'nsew'})]})]})]})])
    style.configure('TButton',padding=(12,8),foreground=GOLD,font=('Georgia',10,'bold'),focuscolor='#d2aa40',anchor='center')
    style.map('TButton',foreground=[('disabled','#7c7254'),('active','#ffe86a')])
    style.configure('TEntry',fieldbackground=FIELD,foreground=GOLD,insertcolor=GOLD,padding=6,
                    bordercolor=BORDER,lightcolor='#514326',darkcolor='#080a09',font=('Georgia',10,'bold'))
    for name,color,text in [('Binding',FIELD,GOLD),('Listening',SELECTED,'#fff0a0'),('Conflict','#3b181c','#ff7373')]:
        style.configure(name+'.TEntry',padding=6,borderwidth=0,relief='flat',font=('Georgia',10,'bold'))
        style.map(name+'.TEntry',fieldbackground=[('readonly',color)],foreground=[('readonly',text)],bordercolor=[('readonly',color)],lightcolor=[('readonly',color)],darkcolor=[('readonly',color)])
    style.configure('TCombobox',fieldbackground=FIELD,foreground=GOLD,background=FIELD,arrowcolor=GOLD,padding=5,bordercolor=BORDER)
    style.map('TCombobox',fieldbackground=[('readonly',FIELD)],foreground=[('readonly',GOLD)],selectbackground=[('readonly',SELECTED)],selectforeground=[('readonly',GOLD)])
    style.configure('Vertical.TScrollbar',background='#302c20',troughcolor='#0b0f10',arrowcolor='#b59a52',bordercolor='#493e26',lightcolor='#685735',darkcolor='#090c0b',arrowsize=12)
    root.option_add('*TCombobox*Listbox.background',FIELD)
    root.option_add('*TCombobox*Listbox.foreground',GOLD)
    root.option_add('*TCombobox*Listbox.selectBackground',SELECTED)
    root.option_add('*TCombobox*Listbox.selectForeground','#fff0a0')
    root._wc3_images = images
    return style
