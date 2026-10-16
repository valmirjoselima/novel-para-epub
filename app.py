import json, os, re, threading, time, zipfile, uuid, html, queue
from pathlib import Path
from urllib.parse import urljoin, urlparse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import requests
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET

APP = 'Novel para EPUB v0.1.0 Beta'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; NovelParaEPUB/0.1; personal ebook converter)'}

def safe_name(s):
    return re.sub(r'[\\/:*?"<>|]+', '_', s).strip() or 'livro'

def url_for_number(url, n):
    match = re.search(r'(cap[ií]tulo[-_ ]?)(\d+)', url, re.I)
    if not match:
        raise ValueError('Não foi encontrado um número de capítulo no link. Use a navegação por próximo capítulo.')
    return url[:match.start(2)] + str(n) + url[match.end(2):]

def clean_html(soup):
    for el in soup.select('script,style,noscript,iframe,nav,footer,header,form,.ads,.advertisement,.sharedaddy,.social-share,.jp-relatedposts'):
        el.decompose()
    candidates = soup.select('article .entry-content, .reading-content, .chapter-content, .text-left, .entry-content, .post-content, article, main')
    def score(el):
        text = el.get_text(' ', strip=True)
        return len(text) + 200 * len(el.select('p')) if len(text) > 150 else 0
    root = max(candidates, key=score, default=None)
    if not root or score(root) < 200:
        raise ValueError('Não foi possível localizar o texto do capítulo; revise manualmente a página.')
    for el in root.select('script,style,aside,nav,.ads,.advertisement,.sharedaddy,.post-navigation,.nav-links,.comments-area,.related-posts'):
        el.decompose()
    blocks = []
    for el in root.find_all(['p','h2','h3','blockquote','li']):
        if el.find_parent(['p','h2','h3','blockquote','li']):
            continue
        text = el.get_text(' ', strip=True)
        if len(text) < 2 or re.search(r'^(próximo capítulo|capítulo anterior|compartilhe|leia também|publicidade)', text, re.I):
            continue
        blocks.append(text)
    if not blocks:
        blocks = [x.strip() for x in root.get_text('\n', strip=True).split('\n') if len(x.strip()) > 2]
    content = '\n\n'.join(blocks)
    if len(content) < 150:
        raise ValueError('Texto muito curto; pode ter ocorrido um erro de extração.')
    return content

def get_chapter(session, url):
    response = session.get(url, timeout=25, headers=HEADERS)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')
    title_el = soup.select_one('h1.entry-title, h1.post-title, h1')
    title = title_el.get_text(' ', strip=True) if title_el else 'Capítulo'
    content = clean_html(soup)
    next_url = None
    for a in soup.select('a[rel="next"], a'):
        label = a.get_text(' ', strip=True).lower()
        if a.get('rel') == ['next'] or ('próximo' in label and 'cap' in label) or ('next chapter' in label):
            href = a.get('href')
            if href:
                next_url = urljoin(response.url, href)
                break
    return {'title':title, 'text':content, 'url':response.url, 'next':next_url, 'status':'OK'}

def make_epub(path, title, author, description, volume, chapters, cover=None):
    if not chapters:
        raise ValueError('Não há capítulos para exportar.')
    bookid = str(uuid.uuid4())
    esc = html.escape
    def xhtml(title_, text):
        paras = ''.join('<p>'+esc(p).replace('\n','<br/>')+'</p>' for p in text.split('\n\n') if p.strip())
        return ('<?xml version="1.0" encoding="utf-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>'+esc(title_)+'</title>'
                '<link rel="stylesheet" type="text/css" href="style.css"/></head><body><h1>'+esc(title_)+'</h1>'+paras+'</body></html>')
    title_full = title + (' — Volume '+str(volume) if volume else '')
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="css" href="style.css" media-type="text/css"/>']
    spine = []
    for i in range(len(chapters)):
        manifest.append(f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{i}"/>')
    cover_data, cover_ext, cover_mime = None, None, None
    if cover and os.path.isfile(cover):
        ext = Path(cover).suffix.lower()
        if ext in ('.jpg','.jpeg','.png'):
            cover_ext = 'jpg' if ext in ('.jpg','.jpeg') else 'png'
            cover_mime = 'image/jpeg' if cover_ext == 'jpg' else 'image/png'
            cover_data = Path(cover).read_bytes()
            manifest.append(f'<item id="cover" href="cover.{cover_ext}" media-type="{cover_mime}" properties="cover-image"/>')
    nav_items = ''.join(f'<li><a href="c{i}.xhtml">{esc(ch["title"])}</a></li>' for i,ch in enumerate(chapters))
    nav = '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Sumário</title></head><body><nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc" id="toc"><h1>Sumário</h1><ol>'+nav_items+'</ol></nav></body></html>'
    opf = ('<?xml version="1.0" encoding="utf-8"?>'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<dc:identifier id="bookid">urn:uuid:'+bookid+'</dc:identifier>'
           '<dc:title>'+esc(title_full)+'</dc:title><dc:creator>'+esc(author or 'Desconhecido')+'</dc:creator>'
           '<dc:language>pt-BR</dc:language><dc:description>'+esc(description or '')+'</dc:description>'
           '<meta property="dcterms:modified">'+time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())+'</meta>'
           '</metadata><manifest>'+''.join(manifest)+'</manifest><spine>'+''.join(spine)+'</spine></package>')
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('mimetype','application/epub+zip',compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml','<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr('OEBPS/content.opf',opf)
        z.writestr('OEBPS/nav.xhtml',nav)
        z.writestr('OEBPS/style.css','body{font-family:serif;line-height:1.6;margin:5%;}p{text-indent:1.3em;margin:0 0 .7em;}h1{text-align:center;margin:1em 0;}')
        if cover_data:
            z.writestr('OEBPS/cover.'+cover_ext,cover_data)
        for i,ch in enumerate(chapters):
            z.writestr(f'OEBPS/c{i}.xhtml',xhtml(ch['title'],ch['text']))

class App:
    def __init__(self,root):
        self.root=root; root.title(APP); root.geometry('1030x760'); root.minsize(800,600)
        self.chapters=[]; self.events=queue.Queue(); self.cancel=threading.Event(); self.running=False
        self.title=tk.StringVar(); self.author=tk.StringVar(); self.volume=tk.StringVar(value='1')
        self.cover=tk.StringVar(); self.link=tk.StringVar(); self.start=tk.StringVar(value='1'); self.end=tk.StringVar(value='30'); self.method=tk.StringVar(value='Número na URL')
        frm=ttk.Frame(root,padding=12); frm.pack(fill='both',expand=True)
        ttk.Label(frm,text=APP,font=('Segoe UI',17,'bold')).pack(anchor='w')
        ttk.Label(frm,text='Importe somente obras para as quais você tem autorização. Respeite as regras dos sites de origem.').pack(anchor='w',pady=(2,10))
        info=ttk.LabelFrame(frm,text='Dados da obra',padding=9);info.pack(fill='x')
        row=ttk.Frame(info);row.pack(fill='x')
        for label,var,width in [('Título',self.title,34),('Autor',self.author,24),('Volume',self.volume,7)]:
            box=ttk.Frame(row);box.pack(side='left',padx=(0,12));ttk.Label(box,text=label).pack(anchor='w');ttk.Entry(box,textvariable=var,width=width).pack()
        crow=ttk.Frame(info);crow.pack(fill='x',pady=5)
        ttk.Label(crow,text='Capa:').pack(side='left');ttk.Entry(crow,textvariable=self.cover).pack(side='left',fill='x',expand=True,padx=5)
        ttk.Button(crow,text='Selecionar imagem',command=self.choose_cover).pack(side='left')
        ttk.Label(info,text='Sinopse').pack(anchor='w');self.synopsis=tk.Text(info,height=3,wrap='word');self.synopsis.pack(fill='x')
        imp=ttk.LabelFrame(frm,text='Importação',padding=9);imp.pack(fill='x',pady=8)
        ttk.Label(imp,text='URL do primeiro capítulo').pack(anchor='w');ttk.Entry(imp,textvariable=self.link).pack(fill='x')
        opt=ttk.Frame(imp);opt.pack(fill='x',pady=6)
        ttk.Label(opt,text='Inicial').pack(side='left');ttk.Entry(opt,textvariable=self.start,width=6).pack(side='left',padx=5)
        ttk.Label(opt,text='Final').pack(side='left');ttk.Entry(opt,textvariable=self.end,width=6).pack(side='left',padx=5)
        ttk.Label(opt,text='Método').pack(side='left',padx=(14,3))
        ttk.Combobox(opt,textvariable=self.method,values=['Número na URL','Link próximo capítulo'],state='readonly',width=22).pack(side='left')
        self.go=ttk.Button(opt,text='Importar capítulos',command=self.import_chapters);self.go.pack(side='left',padx=12)
        ttk.Button(opt,text='Parar',command=self.cancel.set).pack(side='left')
        ttk.Label(imp,text='Intervalo entre páginas: 1,5 s. O programa para em erros e não tenta contornar bloqueios.').pack(anchor='w')
        split=ttk.Panedwindow(frm,orient='horizontal');split.pack(fill='both',expand=True,pady=5)
        left=ttk.Frame(split);right=ttk.Frame(split);split.add(left,weight=1);split.add(right,weight=2)
        ttk.Label(left,text='Capítulos (selecione para revisar)').pack(anchor='w')
        self.list=tk.Listbox(left,exportselection=False);self.list.pack(fill='both',expand=True);self.list.bind('<<ListboxSelect>>',self.select_chapter)
        ttk.Label(right,text='Título do capítulo').pack(anchor='w');self.chapter_title=tk.StringVar();ttk.Entry(right,textvariable=self.chapter_title).pack(fill='x')
        ttk.Label(right,text='Texto (editável)').pack(anchor='w');self.editor=tk.Text(right,wrap='word',undo=True);self.editor.pack(fill='both',expand=True)
        ttk.Button(right,text='Salvar edição do capítulo',command=self.save_edit).pack(anchor='e',pady=4)
        actions=ttk.Frame(frm);actions.pack(fill='x',pady=6)
        for text_,cmd in [('Salvar projeto',self.save_project),('Abrir projeto',self.open_project),('Verificar capítulos',self.validate),('Gerar EPUB',self.export)]:
            ttk.Button(actions,text=text_,command=cmd).pack(side='left',padx=(0,8))
        self.status=tk.StringVar(value='Pronto.');ttk.Label(frm,textvariable=self.status).pack(anchor='w')
        root.after(100,self.poll)
    def choose_cover(self):
        p=filedialog.askopenfilename(filetypes=[('Imagens','*.jpg *.jpeg *.png')]);
        if p:self.cover.set(p)
    def refresh(self):
        self.list.delete(0,'end')
        for i,c in enumerate(self.chapters):self.list.insert('end',f'{i+1:03d} | {c["title"]} | {len(c["text"])} caracteres')
    def select_chapter(self,event=None):
        sel=self.list.curselection()
        if not sel:return
        c=self.chapters[sel[0]];self.chapter_title.set(c['title']);self.editor.delete('1.0','end');self.editor.insert('1.0',c['text'])
    def save_edit(self):
        sel=self.list.curselection()
        if not sel:return
        i=sel[0];self.chapters[i]['title']=self.chapter_title.get().strip();self.chapters[i]['text']=self.editor.get('1.0','end-1c');self.refresh();self.list.selection_set(i);self.status.set('Capítulo atualizado.')
    def import_chapters(self):
        if self.running:return
        try:
            a,b=int(self.start.get()),int(self.end.get())
            if a<1 or b<a or b-a>499:raise ValueError('Escolha um intervalo de até 500 capítulos.')
            url=self.link.get().strip()
            if urlparse(url).scheme not in ('http','https') or not urlparse(url).netloc:raise ValueError('Informe uma URL HTTP(S) válida.')
            if not messagebox.askyesno('Confirmar importação','Você tem autorização para importar este conteúdo e o site permite esse acesso?'):return
        except ValueError as e:messagebox.showerror('Dados inválidos',str(e));return
        self.cancel.clear();self.running=True;self.go.configure(state='disabled');self.chapters=[];self.refresh()
        threading.Thread(target=self.worker,args=(url,a,b,self.method.get()),daemon=True).start()
    def worker(self,url,a,b,method):
        session=requests.Session();seen=set();base_host=urlparse(url).netloc
        try:
            for n in range(a,b+1):
                if self.cancel.is_set():break
                target=url_for_number(url,n) if method=='Número na URL' else url
                if target in seen:raise ValueError('Link repetido: importação interrompida.')
                if urlparse(target).netloc != base_host:raise ValueError('O próximo link aponta para outro domínio.')
                seen.add(target);self.events.put(('status',f'Importando capítulo {n} de {b}...'))
                chapter=get_chapter(session,target)
                if method=='Link próximo capítulo':
                    if not chapter['next'] and n<b:raise ValueError('Não foi encontrado o link do próximo capítulo.')
                    url=chapter['next']
                self.events.put(('chapter',chapter))
                if n<b and not self.cancel.is_set():time.sleep(1.5)
            self.events.put(('status','Importação concluída ou interrompida. Revise os capítulos antes de exportar.'))
        except Exception as e:self.events.put(('error',str(e)))
        finally:self.events.put(('done',None))
    def poll(self):
        try:
            while True:
                kind,val=self.events.get_nowait()
                if kind=='chapter':self.chapters.append(val);self.refresh()
                elif kind=='status':self.status.set(val)
                elif kind=='error':self.status.set('Erro: '+val);messagebox.showwarning('Importação interrompida',str(val))
                elif kind=='done':self.running=False;self.go.configure(state='normal')
        except queue.Empty:pass
        self.root.after(100,self.poll)
    def project(self):
        return {'title':self.title.get(),'author':self.author.get(),'volume':self.volume.get(),'cover':self.cover.get(),'synopsis':self.synopsis.get('1.0','end-1c'),'link':self.link.get(),'start':self.start.get(),'end':self.end.get(),'method':self.method.get(),'chapters':self.chapters}
    def save_project(self):
        p=filedialog.asksaveasfilename(defaultextension='.json',filetypes=[('Projeto JSON','*.json')]);
        if p:Path(p).write_text(json.dumps(self.project(),ensure_ascii=False,indent=2),encoding='utf-8');self.status.set('Projeto salvo.')
    def open_project(self):
        p=filedialog.askopenfilename(filetypes=[('Projeto JSON','*.json')]);
        if not p:return
        try:
            d=json.loads(Path(p).read_text(encoding='utf-8'))
            for key,var in [('title',self.title),('author',self.author),('volume',self.volume),('cover',self.cover),('link',self.link),('start',self.start),('end',self.end),('method',self.method)]:var.set(d.get(key,''))
            self.synopsis.delete('1.0','end');self.synopsis.insert('1.0',d.get('synopsis',''))
            self.chapters=d.get('chapters',[]);self.refresh();self.status.set('Projeto aberto.')
        except Exception as e:messagebox.showerror('Erro ao abrir',str(e))
    def validate(self):
        if not self.chapters:messagebox.showwarning('Verificação','Nenhum capítulo importado.');return
        issues=[];urls=set();texts=set()
        for i,c in enumerate(self.chapters,1):
            if len(c['text'].strip())<500:issues.append(f'Capítulo {i}: texto curto ({len(c["text"])} caracteres)')
            if c['url'] in urls:issues.append(f'Capítulo {i}: URL repetida')
            if c['text'].strip() in texts:issues.append(f'Capítulo {i}: texto duplicado')
            urls.add(c['url']);texts.add(c['text'].strip())
        messagebox.showinfo('Verificação', '\n'.join(issues[:25]) if issues else f'{len(self.chapters)} capítulos sem problemas óbvios. Faça também uma revisão manual.')
    def export(self):
        if self.running:messagebox.showwarning('Aguarde','Finalize a importação antes de exportar.');return
        self.save_edit()
        if not self.title.get().strip() or not self.chapters:messagebox.showwarning('Dados incompletos','Informe o título e importe ao menos um capítulo.');return
        path=filedialog.asksaveasfilename(defaultextension='.epub',initialfile=safe_name(self.title.get()+' - Volume '+self.volume.get())+'.epub',filetypes=[('EPUB','*.epub')])
        if not path:return
        try:
            make_epub(path,self.title.get(),self.author.get(),self.synopsis.get('1.0','end-1c'),self.volume.get(),self.chapters,self.cover.get())
            self.status.set('EPUB gerado: '+path);messagebox.showinfo('Concluído','EPUB gerado com sucesso!')
        except Exception as e:messagebox.showerror('Erro ao gerar EPUB',str(e))

if __name__=='__main__':
    root=tk.Tk();App(root);root.mainloop()
