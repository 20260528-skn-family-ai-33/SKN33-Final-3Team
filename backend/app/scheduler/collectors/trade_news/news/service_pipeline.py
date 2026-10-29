"""수집 원문을 보존한 뒤 서비스 품질·주제를 검사하는 Scrapy 파이프라인."""
from scrapy.exceptions import DropItem
from news.preparation import prepare_article
from news.staging import PreparationStore
from news.dictionary import load_dictionary, validate_dictionary


class ServicePreparationPipeline:
    @classmethod
    def from_crawler(cls, crawler):
        instance = cls()
        path = crawler.settings.get('TAG_DICTIONARY')
        supplied = crawler.settings.get('TAG_DICTIONARY_DATA')
        instance.dictionary = validate_dictionary(supplied) if supplied is not None else load_dictionary(path)
        instance.store = None
        return instance

    def open_spider(self, spider):
        # 기존 수집 폴더 아래에만 준비 결과를 둡니다. 요청 예산은 기존 Store가 관리합니다.
        self.store = PreparationStore(spider.store.root / 'prepared.sqlite3')

    def close_spider(self, spider):
        if self.store:
            self.store.close()

    def process_item(self, item, spider):
        raw = {k: v for k, v in item.items() if not k.startswith('_')}
        prepared = prepare_article(raw, self.dictionary.get('countries', []), self.dictionary.get('products', []), self.dictionary.get('keywords', []))
        self.store.save(raw, prepared)
        if not prepared['eligible']:
            original = item.get('_queue_url', item['source_url'])
            spider.store.mark(original, 'filtered')
            event = 'quality_filtered' if not prepared['quality']['usable_body'] else 'topic_filtered'
            reason = prepared['quality']['reasons'] if event == 'quality_filtered' else prepared['focus']['reason']
            spider.store.event(event, url=item['source_url'], reason=reason)
            spider.crawler.stats.inc_value('articles/' + event)
            raise DropItem(str(reason))
        # body는 원문 그대로 보관합니다. 화면·검색에서 사용할 본문은 별도 필드입니다.
        item['body_masked'] = prepared['body_masked']
        item['preparation'] = {k: prepared[k] for k in ('quality', 'focus', 'cleaning', 'tagging')}
        return item
