import json, os, re, threading, time, zipfile, uuid, html, queue
from pathlib import Path
from urllib.parse import urljoin, urlparse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import requests
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET

APP = 'Novel para EPUB v0.1.3 Beta'
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

def chapter_short_title(title, text, number):
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    for line in lines[:9]:
        m = re.match(r'^(?:cap[ií]tulo|cap\.)\s*(\d+)\s*[-—:–]\s*(.{3,100})$', line, re.I)
        if m and int(m.group(1)) == number:
            return f'Capítulo {number} — {m.group(2).strip()}'
    m = re.search(r'cap[ií]tulo\s*\d+\s*[-—:–]\s*(.{3,100})$', title, re.I)
    if m: return f'Capítulo {number} — {m.group(1).strip()}'
    return f'Capítulo {number}'

def make_epub(path, title, author, description, volume, chapters, cover=None, genres='', alternative='', status='', year='', clean_titles=True):
    if not chapters: raise ValueError('Não há capítulos para exportar.')
    bookid = str(uuid.uuid4()); esc = html.escape
    title_full = title + (' — Volume '+str(volume) if volume else '')
    def page(heading, body, css=True):
        style = '<link rel="stylesheet" type="text/css" href="style.css"/>' if css else ''
        return ('<?xml version="1.0" encoding="utf-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="pt-BR"><head><meta charset="utf-8"/>'
                '<title>'+esc(heading)+'</title>'+style+'</head><body>'+body+'</body></html>')
    def paras(text):
        return ''.join('<p>'+esc(t.strip()).replace('\n','<br/>')+'</p>' for t in re.split(r'\n\s*\n',text) if t.strip())
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="css" href="style.css" media-type="text/css"/>',
                '<item id="intro" href="intro.xhtml" media-type="application/xhtml+xml"/>']
    spine=[]; files={}
    cover_data=None
    if cover and os.path.isfile(cover):
        ext=Path(cover).suffix.lower()
        if ext in ('.jpg','.jpeg','.png'):
            ext='png' if ext=='.png' else 'jpg';mime='image/png' if ext=='png' else 'image/jpeg'
            cover_data=Path(cover).read_bytes()
            manifest += [f'<item id="cover-image" href="images/cover.{ext}" media-type="{mime}" properties="cover-image"/>',
                         '<item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/>']
            spine.append('<itemref idref="cover-page"/>')
            files['cover.xhtml']=page(title_full,f'<div class="cover"><img src="images/cover.{ext}" alt="{esc(title_full)}"/></div>')
            files[f'images/cover.{ext}']=cover_data
    intro='<h1>'+esc(title_full)+'</h1>'
    for label,value in [('Também conhecido como',alternative),('Gêneros',genres),('Autor(es)',author),('Status',status),('Lançamento',year)]:
        if value.strip(): intro+=f'<p><strong>{label}:</strong> {esc(value)}</p>'
    if description.strip():intro+='<h2>Sinopse</h2>'+paras(description)
    intro+=f'<p><strong>Capítulos:</strong> {len(chapters)}</p>'
    files['intro.xhtml']=page('Apresentação',intro)
    spine.append('<itemref idref="intro"/>')
    nav_items=['<li><a href="intro.xhtml">Apresentação</a></li>']
    if cover_data:nav_items.insert(0,'<li><a href="cover.xhtml">Capa</a></li>')
    for i,ch in enumerate(chapters):
        number=int(ch.get('number',i+1))
        heading=chapter_short_title(ch['title'],ch['text'],number) if clean_titles else ch['title']
        text=ch['text']
        # Evita repetir no corpo o cabeçalho que já aparece no título.
        lines=text.splitlines()
        if clean_titles:
            while lines and not lines[0].strip():lines.pop(0)
            if lines and re.sub(r'\s+',' ',lines[0]).strip().casefold()==heading.casefold():lines.pop(0)
            text='\n'.join(lines)
        fname=f'chapter-{i}.xhtml'
        files[fname]=page(heading,'<h2>'+esc(heading)+'</h2>'+paras(text))
        manifest.append(f'<item id="chap-{i}" href="{fname}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="chap-{i}"/>')
        nav_items.append(f'<li><a href="{fname}">{esc(heading)}</a></li>')
    nav=('<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
         'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Sumário</title></head><body>'
         '<nav epub:type="toc" id="toc"><h1>Sumário</h1><ol>'+''.join(nav_items)+'</ol></nav></body></html>')
    opf=('<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">'
         '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
         '<dc:identifier id="bookid">urn:uuid:'+bookid+'</dc:identifier>'
         '<dc:title>'+esc(title_full)+'</dc:title><dc:creator>'+esc(author or 'Desconhecido')+'</dc:creator>'
         '<dc:language>pt-BR</dc:language><dc:description>'+esc(description or '')+'</dc:description>'
         '<meta property="dcterms:modified">'+time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())+'</meta>'
         '</metadata><manifest>'+''.join(manifest)+'</manifest><spine>'+''.join(spine)+'</spine></package>')
    css=('body{font-family:serif;line-height:1.55;margin:4%;}p{margin:0 0 .65em;text-align:justify;}'
         'h1{text-align:center;margin:1em 0;}h2{margin:1.2em 0 .8em;text-align:center;}'
         '.cover{margin:0;padding:0;text-align:center;page-break-after:always;}'
         '.cover img{display:block;width:100%;height:auto;max-height:95vh;object-fit:contain;margin:auto;}')
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('mimetype','application/epub+zip',compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml','<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr('OEBPS/content.opf',opf);z.writestr('OEBPS/nav.xhtml',nav);z.writestr('OEBPS/style.css',css)
        for name,data in files.items():z.writestr('OEBPS/'+name,data)

class App:
    def __init__(self,root):
        self.root=root; root.title(APP); root.geometry('1030x760'); root.minsize(800,600)
        self.chapters=[]; self.events=queue.Queue(); self.cancel=threading.Event(); self.running=False
        self.active_chapter=None; self.loading_chapter=False; self.saved_snapshot=None; self.project_path=None
        self.title=tk.StringVar(); self.author=tk.StringVar(); self.volume=tk.StringVar(value='1')
        self.genres=tk.StringVar(); self.alternative=tk.StringVar(); self.book_status=tk.StringVar(); self.year=tk.StringVar(); self.clean_titles=tk.BooleanVar(value=True)
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
        meta=ttk.Frame(info);meta.pack(fill='x',pady=3)
        for label,var,width in [('Gêneros',self.genres,30),('Nome alternativo',self.alternative,25),('Status',self.book_status,13),('Ano',self.year,8)]:
            cell=ttk.Frame(meta);cell.pack(side='left',padx=(0,9));ttk.Label(cell,text=label).pack(anchor='w');ttk.Entry(cell,textvariable=var,width=width).pack()
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
        actions=ttk.Frame(frm);actions.pack(side='bottom',fill='x',pady=5)
        for text_,cmd in [('Salvar projeto',self.save_project),('Abrir projeto',self.open_project),('Verificar capítulos',self.validate),('Gerar EPUB',self.export)]:
            ttk.Button(actions,text=text_,command=cmd).pack(side='left',padx=(0,8))
        self.status=tk.StringVar(value='Pronto.');ttk.Label(frm,textvariable=self.status).pack(side='bottom',anchor='w')
        ttk.Checkbutton(frm,text='Usar títulos curtos no EPUB (Capítulo 1 — Nome)',variable=self.clean_titles).pack(side='bottom',anchor='w')
        split=ttk.Panedwindow(frm,orient='horizontal');split.pack(fill='both',expand=True,pady=5)
        left=ttk.Frame(split);right=ttk.Frame(split);split.add(left,weight=1);split.add(right,weight=2)
        ttk.Label(left,text='Capítulos (selecione para revisar)').pack(anchor='w')
        self.list=tk.Listbox(left,exportselection=False);self.list.pack(fill='both',expand=True);self.list.bind('<<ListboxSelect>>',self.select_chapter)
        ttk.Label(right,text='Título do capítulo').pack(anchor='w');self.chapter_title=tk.StringVar();ttk.Entry(right,textvariable=self.chapter_title).pack(fill='x')
        ttk.Label(right,text='Texto (editável)').pack(anchor='w');self.editor=tk.Text(right,wrap='word',undo=True);self.editor.pack(fill='both',expand=True)
        ttk.Button(right,text='Salvar alterações',command=self.save_edit).pack(anchor='e',pady=4)
        root.protocol("WM_DELETE_WINDOW",self.on_close)
        self.saved_snapshot=self.snapshot()
        root.after(100,self.poll)
    def choose_cover(self):
        p=filedialog.askopenfilename(filetypes=[('Imagens','*.jpg *.jpeg *.png')]);
        if p:self.cover.set(p)
    def refresh(self):
        self.list.delete(0,'end')
        for i,c in enumerate(self.chapters):self.list.insert('end',f'{i+1:03d} | {c["title"]} | {len(c["text"])} caracteres')
    def snapshot(self):
        return json.dumps(self.project(),ensure_ascii=False,sort_keys=True)
    def commit_active_chapter(self,notify=False):
        if self.active_chapter is None or self.active_chapter >= len(self.chapters):return
        i=self.active_chapter
        title=self.chapter_title.get().strip()
        body=self.editor.get('1.0','end-1c')
        changed=(self.chapters[i]['title']!=title or self.chapters[i]['text']!=body)
        self.chapters[i]['title']=title
        self.chapters[i]['text']=body
        if changed:self.refresh()
        if notify:self.status.set('Alterações do capítulo salvas no projeto em memória. Use Salvar projeto para guardar no disco.')
    def select_chapter(self,event=None):
        sel=self.list.curselection()
        if not sel:return
        index=sel[0]
        if index==self.active_chapter:return
        self.commit_active_chapter()
        self.active_chapter=index
        c=self.chapters[index]
        self.loading_chapter=True
        self.chapter_title.set(c['title'])
        self.editor.delete('1.0','end');self.editor.insert('1.0',c['text'])
        self.loading_chapter=False
        self.list.selection_clear(0,'end');self.list.selection_set(index)
        self.list.see(index)
    def save_edit(self):
        if self.active_chapter is None:
            messagebox.showinfo('Salvar alterações','Selecione um capítulo primeiro.');return
        self.commit_active_chapter(notify=True)
    def clear_active(self):
        self.active_chapter=None
        self.chapter_title.set('')
        self.editor.delete('1.0','end')
    def has_unsaved_changes(self):
        self.commit_active_chapter()
        return self.snapshot()!=self.saved_snapshot
    def confirm_discard(self):
        if not self.has_unsaved_changes():return True
        answer=messagebox.askyesnocancel('Alterações não salvas','O projeto tem alterações não salvas no disco. Deseja salvar antes de continuar?')
        if answer is None:return False
        if answer:
            return self.save_project()
        return True
    def on_close(self):
        if not self.confirm_discard():return
        if self.running:self.cancel.set()
        self.root.destroy()
    def import_chapters(self):
        if self.running:return
        try:
            a,b=int(self.start.get()),int(self.end.get())
            if a<1 or b<a or b-a>499:raise ValueError('Escolha um intervalo de até 500 capítulos.')
            url=self.link.get().strip()
            if urlparse(url).scheme not in ('http','https') or not urlparse(url).netloc:raise ValueError('Informe uma URL HTTP(S) válida.')
            if not messagebox.askyesno('Confirmar importação','Você tem autorização para importar este conteúdo e o site permite esse acesso?'):return
        except ValueError as e:messagebox.showerror('Dados inválidos',str(e));return
        if not self.confirm_discard():return
        self.cancel.clear();self.running=True;self.go.configure(state='disabled');self.chapters=[];self.clear_active();self.refresh()
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
                chapter['number']=n;self.events.put(('chapter',chapter))
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
        return {'title':self.title.get(),'author':self.author.get(),'volume':self.volume.get(),'cover':self.cover.get(),'synopsis':self.synopsis.get('1.0','end-1c'),'link':self.link.get(),'start':self.start.get(),'end':self.end.get(),'method':self.method.get(),'chapters':self.chapters,'genres':self.genres.get(),'alternative':self.alternative.get(),'book_status':self.book_status.get(),'year':self.year.get(),'clean_titles':self.clean_titles.get()}
    def save_project(self):
        self.commit_active_chapter()
        p=self.project_path or filedialog.asksaveasfilename(defaultextension='.json',filetypes=[('Projeto JSON','*.json')])
        if not p:return False
        try:
            payload=self.snapshot()
            Path(p).write_text(json.dumps(json.loads(payload),ensure_ascii=False,indent=2),encoding='utf-8')
            self.project_path=p;self.saved_snapshot=payload
            self.status.set('Projeto salvo: '+p)
            return True
        except Exception as e:
            messagebox.showerror('Erro ao salvar projeto',str(e));return False
    def open_project(self):
        if self.running:messagebox.showwarning('Aguarde','Finalize a importação antes de abrir um projeto.');return
        if not self.confirm_discard():return
        p=filedialog.askopenfilename(filetypes=[('Projeto JSON','*.json')]);
        if not p:return
        try:
            d=json.loads(Path(p).read_text(encoding='utf-8'))
            self.clear_active()
            for key,var in [('title',self.title),('author',self.author),('volume',self.volume),('cover',self.cover),('link',self.link),('start',self.start),('end',self.end),('method',self.method)]:var.set(d.get(key,''))
            self.synopsis.delete('1.0','end');self.synopsis.insert('1.0',d.get('synopsis',''))
            for key,var in [('genres',self.genres),('alternative',self.alternative),('book_status',self.book_status),('year',self.year)]:var.set(d.get(key,''))
            self.clean_titles.set(d.get('clean_titles',True))
            self.chapters=d.get('chapters',[]);self.refresh()
            self.project_path=p;self.saved_snapshot=self.snapshot();self.status.set('Projeto aberto.')
        except Exception as e:messagebox.showerror('Erro ao abrir',str(e))
    def validate(self):
        self.commit_active_chapter()
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
        self.commit_active_chapter()
        if not self.title.get().strip() or not self.chapters:messagebox.showwarning('Dados incompletos','Informe o título e importe ao menos um capítulo.');return
        path=filedialog.asksaveasfilename(defaultextension='.epub',initialfile=safe_name(self.title.get()+' - Volume '+self.volume.get())+'.epub',filetypes=[('EPUB','*.epub')])
        if not path:return
        try:
            make_epub(path,self.title.get(),self.author.get(),self.synopsis.get('1.0','end-1c'),self.volume.get(),self.chapters,self.cover.get(),self.genres.get(),self.alternative.get(),self.book_status.get(),self.year.get(),self.clean_titles.get())
            self.status.set('EPUB gerado: '+path);messagebox.showinfo('Concluído','EPUB gerado com sucesso!')
        except Exception as e:messagebox.showerror('Erro ao gerar EPUB',str(e))

if __name__=='__main__':
    root=tk.Tk();App(root);root.mainloop()
