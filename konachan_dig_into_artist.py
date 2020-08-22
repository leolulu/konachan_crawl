import requests
from lxml import etree
from urllib import parse
import os
from retrying import retry
from concurrent.futures import ThreadPoolExecutor
from tqdm import trange, tqdm
import pickle
from threading import Lock
import traceback
import re


class KonachanInner:
    def __init__(self, page_capacity):
        self.page_capacity = page_capacity
        self.except_artist_tag = 'tagme (artist)'
        self.page_num = 1
        self.base_url = 'http://konachan.com/post'
        self.headers = {
            "Cookie": "vote=1; __utmz=20658210.1460984814.55.2.utmcsr=konachan.com|utmccn=(referral)|utmcmd=referral|utmcct=/post/switch; __cfduid=d18af1d27bb5882a6eff521053cb3cc801525011444; tag-script=; country=US; blacklisted_tags=%5B%22%22%5D; konachan.net=BAh7B0kiD3Nlc3Npb25faWQGOgZFVEkiJTlhYTcwNjk4YzI4NjdjYWU2YjZhYzg2YTZiOWRlZmQ1BjsAVEkiEF9jc3JmX3Rva2VuBjsARkkiMTIxZXdwajVzYVRuR0huSWtWWEUrdlJPOE1EMUdCMHdUdG5yMjFXNmNVNm89BjsARg%3D%3D--8a1e71bdae987934cb60ab31c927084b3c5d85c6; __utmc=20658210; Hm_lvt_ba7c84ce230944c13900faeba642b2b4=1536069861,1536796970,1536939737,1537279770; __utma=20658210.97867196.1446035811.1537370253.1537509624.843; __utmt=1; forum_post_last_read_at=%222018-09-21T08%3A00%3A31%2B02%3A00%22; __utmb=20658210.3.10.1537509624; Hm_lpvt_ba7c84ce230944c13900faeba642b2b4=1537509630",
            "User-Agent": "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/69.0.3497.100 Safari/537.36"
        }
        self.proxies = {
            "http": "socks5://127.0.0.1:10808",
            'https': 'socks5://127.0.0.1:10808'
        }
        self.session = requests.session()
        self.artist_exsist_set = set()
        self.history_handler = pickle_handler('url.history')
        self.history_urls = self.history_handler.load()
        self.lock = Lock()

    def extract(self, item):
        if len(item) == 0:
            return None
        else:
            return item[0]

    def new_folder(self, artist_folder_name):
        '''
        新建文件夹
        '''
        folder_path = './public/konachan/{}'.format(artist_folder_name)
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
        return folder_path

    def artist_exsist_func(self, artist_tag_name):
        """
        作者排重
        包括方法调用
        返回要处理的作者名称
        """
        if artist_tag_name in self.artist_exsist_set:
            print(artist_tag_name, ': 此artist已存在，跳过')
            return None
        elif artist_tag_name == self.except_artist_tag:
            print(artist_tag_name, ': 系通用artist，跳过')
            return None
        else:
            self.artist_exsist_set.add(artist_tag_name)
            return artist_tag_name  # 对于第一次遇到的作者调用处理方法

    def url_compliet(self, url):
        '''
        补全url地址
        '''
        if url is None:
            return None
        else:
            return parse.urljoin(self.base_url, url)

    def parse_url_get_html(self, url):
        '''
        返回可以直接xpath的HTML对象
        '''
        r = self.session.get(url, headers=self.headers, proxies=self.proxies)
        return etree.HTML(r.content)

    @retry(wait_exponential_multiplier=1000, wait_exponential_max=60000)
    def parse_url(self, url):
        '''
        返回requests的content
        '''
        r = self.session.get(url, headers=self.headers, proxies=self.proxies, timeout=60)
        return r.content

    def parse_mainpage(self, page_capacity):
        """
        解析每一个首页
        """
        detail_page_urls_list = []
        for _ in trange(page_capacity):
            html = self.parse_url_get_html('{}?page={}&tags='.format(self.base_url, self.page_num))
            detail_page_urls = html.xpath("//ul[@id='post-list-posts']/li/div/a/@href")
            detail_page_urls = map(self.url_compliet, detail_page_urls)
            detail_page_urls_list.extend(detail_page_urls)
            self.page_num += 1
        return detail_page_urls_list

    def parse_detail_page(self, detail_page_url_list, deal_func):
        """
        解析详情页
        """
        with ThreadPoolExecutor(max_workers=8) as executor:
            for detail_page_url in tqdm(detail_page_url_list):
                html = self.parse_url_get_html(detail_page_url)
                artist_name = html.xpath("//li[contains(@class,'tag-type-artist')]/a[2]/text()")[0]
                artist_url = self.url_compliet(html.xpath("//li[contains(@class,'tag-type-artist')]/a[2]/@href")[0])
                if deal_func(artist_name) is not None:
                    # 1.点击作者进入作者图像列表
                    next_url = artist_url
                    while next_url is not None:
                        html = self.parse_url_get_html(next_url)
                        next_url = self.url_compliet(self.extract(html.xpath("//a[text()='Next →']/@href")))
                        img_url_list = html.xpath("//ul[@id='post-list-posts']/li/a/@href")
                        with self.lock:
                            if img_url_list[0] in self.history_handler.load() and img_url_list[-1] in self.history_handler.load():
                                print(artist_name, 'artist没有新作，pass...')
                                break
                        # 2.获取图像
                        for img_url in img_url_list:
                            executor.submit(self.downloadPic, img_url, artist_name)

    @retry(wait_exponential_multiplier=1000, wait_exponential_max=60000)
    def downloadPic(self, img_url, artist_name):
        try:
            with self.lock:
                if img_url in self.history_handler.load():
                    print(img_url, '历史上已下载过，跳过...')
                    return
                self.history_urls.add(img_url)
                self.history_handler.dump(self.history_urls)
            folder_path = self.new_folder(artist_name)
            url_file_name = os.path.splitext(img_url.split('/')[-1].replace('%20',''))
            pic_path_name = os.path.join(folder_path, re.sub(r"\D", "", url_file_name[0]) + url_file_name[-1])
            print('downloading：', img_url)
            if os.path.exists(pic_path_name) == True:
                # os.remove(pic_path_name)
                pass
            else:
                content = self.parse_url(img_url)
                with open(pic_path_name, 'wb') as f:
                    f.write(content)
        except:
            print('下载出错：', traceback.format_exc())

    def run(self):
        detail_page_urls = self.parse_mainpage(self.page_capacity)  # 解析首页，获得每一个详情页的地址
        self.parse_detail_page(detail_page_urls, self.artist_exsist_func)  # 解析详情页(包括回调函数)


class pickle_handler:
    '''
    p = pickle_handler(file_path)

    p.dump(byte_obj)
    p.load()
    '''

    def __init__(self, file_path):
        self.file_path = file_path
        if not os.path.exists(file_path):
            with open(file_path, 'wb') as f:
                pickle.dump(set(), f)

    def dump(self, byte_obj):
        with open(self.file_path, 'wb') as f:
            pickle.dump(byte_obj, f)

    def load(self):
        with open(self.file_path, 'rb') as f:
            return pickle.load(f)


if __name__ == "__main__":
    k1 = KonachanInner(20)
    k1.run()
