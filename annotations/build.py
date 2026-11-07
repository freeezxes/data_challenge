# -*- coding: utf-8 -*-
import json,hashlib,pathlib,datetime,copy
ROOT=pathlib.Path(__file__).resolve().parents[1]
CHECKLIST=ROOT/'docs/sources/checklist.pdf'
records=json.loads((ROOT/'public/dataset.json').read_text())['data']['records']
byid={r['conversation_id']:r for r in records}
criteria=[
 {'id':'contact','name':'Установление контакта','weight':10,'kind':'boolean','allow_na':False,'rule':'Оператор поздоровался на языке клиента и представился в первом ответе.'},
 {'id':'needs','name':'Выявление потребности','weight':15,'kind':'boolean','allow_na':True,'rule':'Оператор учёл известные детали и уточнил недостающее до ответа. N/A — данных для ответа хватало с самого начала.'},
 {'id':'identification','name':'Идентификация','weight':20,'kind':'boolean','allow_na':True,'rule':'Проверка личности подтверждена до раскрытия данных или защищённого действия. N/A — запрос не требовал идентификации.'},
 {'id':'consultation','name':'Корректная и полная консультация','weight':20,'kind':'scale','scale':[0,5,10,15,20],'max':20,'allow_na':True,'rule':'20 — все существенные вопросы закрыты или дан корректный следующий шаг; 15 — пропущена второстепенная деталь; 10 — открыт существенный вопрос; 5 — общая подсказка без понятного действия; 0 — нет ответа по существу или ключевой совет ошибочен. N/A — запрос не сформулирован до обрыва.'},
 {'id':'empathy','name':'Эмпатия и клиентоориентированность','weight':15,'kind':'boolean','allow_na':False,'rule':'Оператор вежлив и при выраженном недовольстве или неудобстве реагирует на конкретную проблему.'},
 {'id':'management','name':'Управление диалогом, скорость и грамотность','weight':15,'kind':'scale','scale':[0,4,7,10],'max':10,'allow_na':False,'rule':'10 — ясно, связно, при явном ожидании есть предупреждение; 7 — один заметный сбой; 4 — клиент переспрашивает из-за формулировок или порядка; 0 — ход диалога непонятен из-за бессвязности или противоречий. Порог первой реакции в 3 минуты не применяется.'},
 {'id':'closing','name':'Завершение','weight':5,'kind':'boolean','allow_na':True,'rule':'Оператор подвёл итог или предложил дальнейшую помощь и попрощался. N/A — разговор оборвался до возможности завершения.'},
 {'id':'critical','name':'Грубые ошибки','weight':0,'kind':'flag','allow_na':False,'rule':'Оскорбление, предложение платной услуги в обход компании или раскрытие конфиденциальной информации. Отдельный флаг, итог автоматически не обнуляется.'}
]
def rationale(c,v):
 i=c['id']
 rules={
 ('contact','Y'):'В первом ответе оператора есть приветствие на языке клиента и представление.',
 ('contact','N'):'Первый ответ оператора не содержит полного сочетания приветствия на языке клиента и представления.',
 ('needs','Y'):'Недостающая существенная деталь уточнена перед соответствующим ответом; известные детали использованы.',
 ('needs','N'):'Оператор не уточнил необходимое или повторно запросил уже известные сведения.',
 ('needs','NA'):'К моменту ответа оператора запрос и необходимые для общего ответа детали уже содержались в истории; новое уточнение не требовалось.',
 ('identification','Y'):'В истории подтверждена проверка личности до раскрытия индивидуальных данных или защищённого действия.',
 ('identification','N'):'Для индивидуальной проверки или защищённого действия нужна идентификация, но её завершение до действия не подтверждено.',
 ('identification','NA'):'Дана общая инструкция или справочная информация; индивидуальные сведения по счёту не раскрывались и защищённое действие не выполнялось.',
 ('consultation','20'):'Все существенные вопросы получили содержательный ответ либо понятный следующий шаг. Оценка по содержанию транскрипта, без независимой корпоративной базы знаний.',
 ('consultation','15'):'Основной запрос решён, но второстепенная деталь осталась без ответа.',
 ('consultation','10'):'Часть запроса решена, но существенный вопрос остался открытым.',
 ('consultation','5'):'Есть только общая подсказка; клиенту не объяснено достаточно конкретно, как действовать.',
 ('consultation','0'):'Нет ответа по существу либо ключевая рекомендация ошибочна.',
 ('consultation','NA'):'Разговор прервался до формулирования содержательного запроса клиента.',
 ('empathy','Y'):'Реплики оператора вежливы и ориентированы на конкретный запрос; выраженное неудобство, если оно было, не оставлено без содержательной реакции.',
 ('empathy','N'):'Оператор не реагирует на явно выраженную проблему клиента либо общается невежливо.',
 ('management','10'):'Ответы понятны и последовательны; в проверенном фрагменте нет явного ожидания без предупреждения. Формальный порог в 3 минуты не применяется.',
 ('management','7'):'Один заметный сбой, после которого клиент может понять ответ и продолжить.',
 ('management','4'):'Формулировки или порядок ответов заставляют клиента переспрашивать.',
 ('management','0'):'Из-за бессвязных или противоречивых ответов ход диалога непонятен.',
 ('closing','Y'):'В завершении есть итог или приглашение обратиться за дальнейшей помощью, а также прощание.',
 ('closing','N'):'В завершении нет полного сочетания итога/дальнейшей помощи и прощания. Одного пожелания хорошего дня по принятой строгой трактовке недостаточно.',
 ('closing','NA'):'Системное событие или обрыв на стороне клиента не дали оператору возможности завершить разговор.',
 ('critical','N'):'Проверены все реплики оператора в указанном диапазоне. Оскорблений, предложения частной платной услуги и раскрытия конфиденциальных данных не обнаружено.',
 ('critical','Y'):'В репликах оператора есть грубое нарушение из закрытого перечня чек-листа.'}
 return rules[(i,str(v))]
def parse_refs(s,r):
 if ':' in s:
  start,end=map(int,s.split(':'));refs=sorted(set([start,end]));span=[start,end]
 else:refs=list(map(int,s.split(',')));span=None
 for n in refs:assert 1<=n<=len(r['transcript']),(r['conversation_id'],n)
 ev=[{'message_index':n,'timestamp':r['transcript'][n-1]['timestamp'],'speaker':r['transcript'][n-1]['speaker'],'quote':r['transcript'][n-1]['text']} for n in refs]
 return ev,span

def build():
 details=json.loads((ROOT/'annotations/details.json').read_text());out=[]
 for line in (ROOT/'annotations/decisions.tsv').read_text().splitlines():
  if not line.strip() or line.startswith('#'):continue
  fields=line.split('|');assert len(fields)==10,(fields[0],len(fields));id=fields[0];r=byid[id];extra=details.get(id,{})
  verdicts=[]
  for index,(c,decision) in enumerate(zip(criteria,fields[1:9]),1):
   raw,refs=decision.split('@');value={'Y':'Да','N':'Нет','NA':'N/A'}.get(raw,int(raw) if raw.isdigit() else raw)
   if raw=='NA':assert c['allow_na'],(id,c['id'])
   elif c['kind']=='scale':assert value in c['scale'],(id,c['id'],value)
   else:assert raw in ['Y','N'],(id,c['id'],raw)
   ev,span=parse_refs(refs,r)
   note=extra.get('review',{}).get(str(index))
   if c['id']=='closing' and raw=='N' and not note:note='Строгая трактовка завершения: прощание без итога/предложения дальнейшей помощи не засчитывается. Стоит согласовать трактовку с человеком.'
   points=None if raw=='NA' else (value/c['max']*c['weight'] if c['kind']=='scale' else c['weight'] if raw=='Y' else 0)
   verdicts.append({'criterion_id':c['id'],'value':value,'raw_points':points,'rationale':extra.get(str(index),rationale(c,raw)), 'evidence':ev,'inspected_range':span,'review_required':bool(note),'review_note':note})
  applicable=sum(c['weight'] for c,v in zip(criteria,verdicts) if v['value']!='N/A')
  earned=sum(v['raw_points'] or 0 for v in verdicts)
  score=round(100*earned/applicable+1e-10,2) if applicable else None
  out.append({'conversation_id':id,'status':'annotated','author':'assistant_manual','summary':fields[9],'transcript_sha256':hashlib.sha256(json.dumps(r['transcript'],ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),'criteria':verdicts,'applicable_weight':applicable,'earned_weighted_points':earned,'total_score':score,'critical_flag':verdicts[7]['value']=='Да','review_required':any(v['review_required'] for v in verdicts),'channel_scope':'transcript_only' if r['channel']=='voice call' else 'chat_transcript','notes':extra.get('notes',[])})
 assert len({r['conversation_id'] for r in out})==len(out),'Duplicate annotation'
 result={'schema_version':'1.0','annotation_version':'assistant-v1-2026-11-07','provenance':{'author':'assistant_manual','method':'Решения выставлены ассистентом после чтения каждого транскрипта; скрипт только формирует ссылки на исходные реплики и считает баллы. Внешний оценщик не вызывался.','official_ground_truth':False,'human_reviewed':False,'source_dataset_sha256':hashlib.sha256((ROOT/'public/dataset.json').read_bytes()).hexdigest(),'checklist_source':'CQA_Chat_Quality_Checklist 1.pdf, Telegram Data Challenge26, topic 5, message 2310','checklist_sha256':hashlib.sha256(CHECKLIST.read_bytes()).hexdigest()},'policy_notes':['Это наша предварительная разметка ассистентом, а не официальный эталон организаторов и не разметка человеком.','Оцениваются только действия оператора; бот и системные события используются как контекст.','Применена строгая трактовка завершения: итог либо дальнейшая помощь плюс прощание. Пограничные решения помечены review_required.','Нет независимой базы знаний брокера. Фактическая корректность инструкций оценивается по содержанию и согласованности диалога; продуктовые утверждения не считаются независимо проверенными.','Для звонков оценён подготовленный транскрипт. Ошибки ASR и скрытые персональные данные не считаются ошибками речи оператора. Дикция и интонация не оценивались.','Критерий грубых ошибок имеет нулевой вес и не обнуляет итог.','Ожидание оценивается по явным репликам об ожидании; порог первой реакции 3 минуты исключён.','Для будущего harness спорные критерии следует проверять отдельно; статус annotated означает завершённый разбор, а не отсутствие неоднозначности.'],'criteria':criteria,'counts':{'annotated':len(out),'dataset':len(records),'review_required':sum(r['review_required'] for r in out),'critical':sum(r['critical_flag'] for r in out)},'annotations':out}
 (ROOT/'public/annotations.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
 print(json.dumps(result['counts'],ensure_ascii=False));return result
if __name__=='__main__':build()
