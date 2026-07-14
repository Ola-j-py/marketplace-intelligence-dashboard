from __future__ import annotations
import base64, os, re, statistics
from urllib.parse import quote_plus, urlparse
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from flask import Flask, render_template, request
load_dotenv(); app=Flask(__name__)
CID=os.getenv('EBAY_CLIENT_ID',''); SECRET=os.getenv('EBAY_CLIENT_SECRET',''); MARKET=os.getenv('EBAY_MARKETPLACE_ID','EBAY_US')
DEMO=os.getenv('DEMO_MODE','true').lower()=='true'
HARD=['junk','ジャンク','for parts','not working','動作未確認','broken']
WARN=['untested','as-is','現状品','scratches','傷あり','missing','no returns']
def platform(url):
 h=urlparse(url).netloc.lower()
 if 'ebay.' in h:return 'eBay'
 if 'amazon.' in h:return 'Amazon'
 if 'mercari.' in h:return 'Mercari'
 if 'yahoo.' in h:return 'Yahoo Auctions'
 return h or 'Unknown'
def iid(url):
 for p in [r'/itm/(?:[^/]+/)?(\d{9,15})',r'[?&]item=(\d{9,15})']:
  m=re.search(p,url)
  if m:return m.group(1)
 return ''
def metadata(url):
 p=urlparse(url)
 if p.scheme not in {'http','https'} or not p.netloc: raise ValueError('有効な商品URLを入力してください。')
 r=requests.get(url,headers={'User-Agent':'Mozilla/5.0'},timeout=8); r.raise_for_status(); soup=BeautifulSoup(r.text,'html.parser')
 def meta(a,v):
  t=soup.find('meta',attrs={a:v}); return t.get('content','').strip() if t else ''
 title=meta('property','og:title') or meta('name','twitter:title') or (soup.title.get_text(' ',strip=True) if soup.title else '')
 desc=meta('property','og:description') or meta('name','description'); image=meta('property','og:image')
 price=0.0
 try: price=float((meta('property','product:price:amount') or '0').replace(',',''))
 except: pass
 mm=re.findall(r'\b[A-Z0-9]{2,}(?:[-_/][A-Z0-9]{2,})+\b',f'{title} {desc}'.upper())
 return {'title':title or 'Unknown product','description':desc or 'No structured description found.','image':image,'model':mm[0] if mm else '', 'source':platform(url),'url':url,'listed_price':price}
def token():
 enc=base64.b64encode(f'{CID}:{SECRET}'.encode()).decode()
 r=requests.post('https://api.ebay.com/identity/v1/oauth2/token',headers={'Authorization':f'Basic {enc}','Content-Type':'application/x-www-form-urlencoded'},data={'grant_type':'client_credentials','scope':'https://api.ebay.com/oauth/api_scope'},timeout=15); r.raise_for_status(); return r.json()['access_token']
def ebay_search(q):
 if not(CID and SECRET): return []
 r=requests.get('https://api.ebay.com/buy/browse/v1/item_summary/search',headers={'Authorization':f'Bearer {token()}','X-EBAY-C-MARKETPLACE-ID':MARKET},params={'q':q,'limit':20},timeout=20); r.raise_for_status(); out=[]
 for x in r.json().get('itemSummaries',[]):
  try:v=float((x.get('price') or {}).get('value') or 0)
  except:v=0
  out.append({'title':x.get('title',''),'price':v,'condition':x.get('condition',''),'url':x.get('itemWebUrl','')})
 return out
def demo():
 p={'title':'Sony WH-1000XM5 Wireless Headphones','description':'Used, tested and working. Includes case and charging cable.','image':'','model':'WH-1000XM5','source':'Portfolio Demo','url':'#','listed_price':185.0}
 prices=[219,229,235,239,245,249,258,265,272,279,285,289]
 return p,[{'title':f'Sony WH-1000XM5 comparable {i+1}','price':x,'condition':'Used','url':'#'} for i,x in enumerate(prices)]
def analyze(p,comps,override,shipping,fees):
 purchase=override if override>0 else float(p.get('listed_price') or 0); prices=[x['price'] for x in comps if x['price']>0]; bench=round(statistics.median(prices),2) if prices else 0.0
 text=f"{p['title']} {p['description']}".lower(); risk=0; ev=[]
 for t in HARD:
  if t in text:risk+=45;ev.append(('bad',f'Hard-reject term detected: {t}'))
 for t in WARN:
  if t in text:risk+=10;ev.append(('warn',f'Condition warning detected: {t}'))
 profit=round(bench-purchase-shipping-fees,2) if bench else 0; roi=round(profit/purchase*100,1) if purchase else 0; margin=round(profit/bench*100,1) if bench else 0
 if len(prices)>=5:ev.append(('good',f'{len(prices)} comparable listings support the benchmark.'))
 else:risk+=12;ev.append(('warn','The market comparison sample is limited.'))
 if not bench:risk+=20;ev.append(('warn','No automatic market benchmark is available yet.'))
 elif profit<=0:risk+=30;ev.append(('bad','Estimated profit is not positive.'))
 elif roi<20:risk+=10;ev.append(('warn','Estimated ROI is below 20%.'))
 else:ev.append(('good','Estimated ROI clears the 20% threshold.'))
 risk=min(risk,100); status='REJECT' if risk>=60 else 'REVIEW' if risk>=30 else 'BUY'; conf=min(96,30+min(40,len(prices)*3)+(15 if p.get('model') else 0))
 return {'purchase':purchase,'benchmark':bench,'profit':profit,'roi':roi,'margin':margin,'risk':risk,'status':status,'confidence':conf,'evidence':ev}
def links(q):
 q=quote_plus(q); return [('eBay sold search',f'https://www.ebay.com/sch/i.html?_nkw={q}&LH_Sold=1&LH_Complete=1'),('Google research',f'https://www.google.com/search?q={q}+price+sold+used'),('Mercari search',f'https://www.mercari.com/search/?keyword={q}'),('Yahoo Auctions',f'https://auctions.yahoo.co.jp/search/search?p={q}'),('Amazon search',f'https://www.amazon.com/s?k={q}')]
@app.route('/',methods=['GET','POST'])
def index():
 result=None
 if request.method=='POST':
  try:
   mode=request.form.get('mode','url'); override=float(request.form.get('purchase_override') or 0); shipping=float(request.form.get('shipping_cost') or 0); fees=float(request.form.get('fees') or 0); notes=[]
   if mode=='demo':p,comps=demo();notes.append('Portfolio demo uses clearly labeled sample comparisons.')
   else:
    url=request.form.get('product_url','').strip()
    if not url:raise ValueError('商品URLを入力してください。')
    try:p=metadata(url)
    except requests.RequestException:
     x=iid(url);p={'title':f'{platform(url)} product {x}'.strip(),'description':'Marketplace blocked direct metadata retrieval.','image':'','model':x,'source':platform(url),'url':url,'listed_price':0.0};notes.append('対象サイトが直接取得を拒否したため、URL識別子を使用しました。')
    q=p['model'] or p['title']; comps=ebay_search(q)
    if not comps:notes.append('eBay API未設定または比較結果なし。無料調査リンクを生成しました。')
   a=analyze(p,comps,override,shipping,fees); q=p['model'] or p['title']; result={'product':p,'comparisons':comps[:6],'analysis':a,'links':links(q),'notes':notes}
  except Exception as e:result={'error':str(e)}
 return render_template('index.html',result=result,demo_enabled=DEMO,api_enabled=bool(CID and SECRET))
if __name__=='__main__':app.run(host='127.0.0.1',port=5136,debug=True)
