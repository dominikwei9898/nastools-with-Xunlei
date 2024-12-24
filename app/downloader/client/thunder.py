from app.downloader.client._base import _IDownloadClient
from app.utils import ExceptionUtils, RequestUtils
from app.utils.types import DownloaderType
import time
import base64
import hashlib
import log
import re
import requests
from urllib.parse import quote
from app.utils.torrent import Torrent
import bencodepy
from app.utils import StringUtils
import json
import datetime
import io
from requests_toolbelt import MultipartEncoder


class Thunder(_IDownloadClient):
    # 下载器ID
    client_id = "thunder"
    # 下载器类型
    client_type = DownloaderType.THUNDER
    # 下载器名称
    client_name = "迅雷"
    
    @staticmethod
    def match(ctype):
        """
        匹配下载器类型
        :param ctype: 下载器类型
        :return: 是否匹配
        """
        return True if ctype in ["thunder", "迅雷"] else False
        
    _token = None
    _token_expire = None
    _device_id = None
    _pan_auth = None
    _base_url = None
    _folder_name = "downloads"  # 默认文件夹名称

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.host = config.get('host')
        self.port = config.get('port')
        self.username = config.get('username')
        self.password = config.get('password')
        self.download_dir = config.get('download_dir')
        self._client = None
        
        # 初始化基础URL
        if self.host and self.port:
            self._base_url = f"http://{self.host}:{self.port}/webman/3rdparty/pan-xunlei-com/index.cgi"
        
        # 初始化客户端
        self._initialize()
        
        # 添加目录ID缓存
        self._folder_id_cache = {}
        self._folder_id_cache_time = {}
        self._cache_expire_time = 300  # 缓存过期时间(秒)

    def _initialize(self):
        """
        初始化客户端
        """
        if not self._base_url:
            return
            
        try:
            # 获取认证信息
            self._get_auth()
        except Exception as e:
            ExceptionUtils.exception_traceback(e)

    def _make_request(self, method: str, endpoint: str, **kwargs):
        """
        发送 HTTP 请求的通用方法
        """
        try:
            if endpoint.startswith('http'):
                url = endpoint
            else:
                if not endpoint.startswith('/'):
                    endpoint = '/' + endpoint
                url = f"{self._base_url}{endpoint}"

            # 创建 session 来维持 cookies
            session = requests.Session()
            session.auth = (self.username, self.password)

            # 默认 headers
            default_headers = {
                'DNT': '1',
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                'device-space': '',
                'content-type': 'application/json',
                'Accept': '*/*',
                'Accept-Language': 'zh-CN,zh;q=0.9'
            }

            # 如果不是获取 pan-auth 的请求，且已有 pan-auth，则添加到 header
            if not endpoint.endswith('/') and self._pan_auth:
                default_headers['pan-auth'] = self._pan_auth

            # 合并自定义 headers
            headers = {**default_headers, **kwargs.pop('headers', {})}
            response = session.request(
                method,
                url,
                headers=headers,
                **kwargs
            )

            if response.status_code != 200:
                log.error(f"请求失败，状态码: {response.status_code}")
                log.error(f"响应头: {dict(response.headers)}")
                log.error(f"响应内容: {response.text}")
                response.raise_for_status()

            # 对于获取 pan-auth 的请求，直接返回响应对象
            if endpoint.endswith('/'):
                return response

            result = response.json()
            log.debug(f"响应状态码: {response.status_code}")
            log.debug(f"响应内容: {result}")

            return result

        except Exception as e:
            log.error(f"请求失败: {str(e)}")
            log.error(f"请求URL: {url}")
            raise

    def _get_auth(self):
        """
        获取认证令牌
        """
        if self._token and self._token_expire and self._token_expire > time.time():
            return True
        
        try:
            url = "/"
            log.info(f"正在请求认证信息")
            
            # 使用通用请求方法
            res = self._make_request('GET', url)
            
            if res and res.status_code == 200:
                # 从响应中提取token
                uiauth = r'function uiauth\(value\){ return "(.*)" }'
                matches = re.findall(uiauth, res.text)
                if matches:
                    self._pan_auth = matches[0]
                    self._token = self._pan_auth
                    self._token_expire = time.time() + 600
                    log.info(f"成功获取token: {self._pan_auth}")
                    return True
                else:
                    log.error(f"在响应中未找到令牌模式")
                    return False
            return False
            
        except Exception as e:
            log.error(f"认证请求异常: {str(e)}")
            ExceptionUtils.exception_traceback(e)
            return False

    def get_type(self):
        """
        获取下载器类型
        """
        return self.client_type

    def get_status(self):
        """
        检查连通性
        """
        try:
            # 检查参数
            if not self.host or not self.port:
                return False
                
            # 获取认证
            if not self._get_auth():
                return False
                
            # 检查设备状态
            device_id = self.get_device_id()
            if device_id:
                self._device_id = device_id
                return True
                
            return False
            
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            return False

    def get_torrents(self, ids=None, status=None, **kwargs):
        """
        获取下载器中的种列表
        """
        if not self._client:
            return []
        
        try:
            # 强制刷新任务列表
            self._client.get_tasks()
            
            # 获取所有正在下载的任务
            downloading_tasks = self._client.get_downloading_tasks() or []
            log.info(f"获取正在下载的任务: {downloading_tasks}")    
            # 获取正在等待的任务
            waiting_tasks = self._client.get_waiting_tasks() or []
            log.info(f"获取正在等待的任务: {waiting_tasks}")
            # 合并任务列表
            all_tasks = downloading_tasks + waiting_tasks
            
            if not all_tasks:
                return []
            
            ret_torrents = []
            for task in all_tasks:
                if not task:
                    continue
                # 转换为统一的种子信息格式
                ret_torrents.append(self._parse_thunder_task_to_torrent(task))
            
            return ret_torrents
        except Exception as e:
            log.error(f"【{self.client_name}】获取种子列表出错: {str(e)}")
            return []

    def _parse_thunder_task_to_torrent(self, task):
        """
        转换迅雷下载任务为统一格式
        """
        # 计算进度
        try:
            progress = round(float(task.get("progress", 0)) * 100, 1)
        except:
            progress = 0
        
        return {
            'id': task.get('id'),
            'name': task.get('name'),
            'speed': StringUtils.str_filesize(task.get('speed', 0)) + "/s",
            'state': "Downloading" if task.get("state") in ["downloading", "waiting"] else "Stoped",
            'progress': progress,
            # 其他需要的字段...
        }

    def get_completed_torrents(self, ids=None, tag=None):
        """
        获取已完成的种子列表
        :param ids: 种子ID列表
        :param tag: 种子标签
        :return: 种子信息列表, 是否出错
        """
        return self.get_torrents(status="completed", ids=ids, tag=tag)

    def get_downloading_torrents(self, ids=None, tag=None):
        """
        获取正在下载的种子列表
        :param ids: 种子ID列表
        :param tag: 种子标签
        :return: 种子信息列表, 是否出错
        """
        return self.get_torrents(status="downloading", ids=ids, tag=tag)

    def add_torrent(self, content, download_dir=None, **kwargs):
        """
        添加种子
        :param content: 种子内容/磁力链接
        :param download_dir: 下载目录路径
        :param kwargs: 
            - selected_files: 选中的文件索引列表
            - file_name: 指定文件名
        :return: bool
        """
        if not content:
            log.error("未提供下载内容")
            return False
        
        try:
            log.info(f"开始添加下载任务...")
            log.info(f"下载目录: {download_dir}")
            
            # 获取认证
            if not self._get_auth():
                log.error("获取认证失败")
                return False
            
            # 确保有device_id
            if not self._device_id:
                log.info("尝试重新获取设备ID...")
                self._device_id = self.get_device_id()
                
            if not self._device_id:
                log.error("无法获取设备ID,终止下载")
                return False
            
            log.info(f"当前设备ID: {self._device_id}")
            
            # 处理种子内容
            if isinstance(content, str):
                if content.startswith('magnet:'):
                    magnet_link = content
                    log.info(f"处理磁力链接: {magnet_link}")
                elif content.startswith('http'):
                    log.error("暂不支持URL下载")
                    return False
                else:
                    log.error(f"不支持的链接类型: {content[:100]}...")
                    return False
            else:
                # 验证种子文件
                try:                    # 直接使用bencodepy验证种子文件
                    metadata = bencodepy.decode(content)
                    if not metadata or b'info' not in metadata:
                        log.error("无效的种子文件")
                        return False
                        
                    magnet_link = self._torrent2magnet(content)
                    if not magnet_link:
                        log.error("种子文件转换失败")
                        return False
                        
                    log.info(f"种子转换成功: {magnet_link}")
                except Exception as e:
                    log.error(f"种子文件处理失败: {str(e)}")
                    return False

            # 检查任务是否已存在
            existing_tasks = self.get_torrents() or []  # 直接使用返回的列表
            for task in existing_tasks:
                if task.get('params', {}).get('url') == magnet_link:
                    log.info(f"任务已存在,跳过添加")
                    return True

            # 获取资源列表
            log.info("正在获取资源信息...")
            list_result = self._make_request(
                'POST',
                "/drive/v1/resource/list",
                params={"device_space": ""},
                json={
                    "urls": magnet_link,
                    "sub_file_index": "",
                    "space": self._device_id,
                    "check_exists": True
                },
                timeout=60
            )
            
            log.debug(f"获取资源列表结果: {list_result}")
            if not list_result.get('list', {}).get('resources'):
                log.error(f"未找到可下载的资源: {list_result}")
                return False
            
            resource = list_result['list']['resources'][0]
            task_name = kwargs.get('file_name') or resource.get('name')
            
            # 修改文件大小获取逻辑
            file_size = 0
            if resource.get('is_dir') and resource.get('dir', {}).get('resources'):
                # 如果是目录，累加所有文件的大小
                for file in resource['dir']['resources']:
                    file_size += int(file.get('file_size', 0))
                log.info(f"目录总大小: {file_size} bytes ({len(resource['dir']['resources'])} 个文件)")
            else:
                # 单文件直接获取大小
                file_size = int(resource.get('file_size', 0))
                log.info(f"单文件大小: {file_size} bytes")
            
            log.info(f"资源名称: {task_name}")
            log.info(f"文件大小: {StringUtils.str_filesize(file_size)}")
            
            # 获取或创建下载目录
            log.info(f"开始处理下载目录: {download_dir}")
            parent_folder_id = self._get_folder_id(download_dir)
            if not parent_folder_id:
                log.error(f"下载目录不存在: {download_dir}")
                return False
            
            log.info(f"下载目录ID: {parent_folder_id}")
            
            # 处理子文件选择
            selected_files = kwargs.get('selected_files', [])
            sub_file_index = ','.join(map(str, selected_files)) if selected_files else ""
            
            # 构建下载任务参数
            task_params = {
                "type": "user#download-url",
                "name": task_name,
                "file_name": task_name,
                "file_size": str(file_size),
                "space": self._device_id,
                "params": {
                    "target": self._device_id,
                    "url": magnet_link,
                    "total_file_count": str(len(selected_files) if selected_files else 1),
                    "parent_folder_id": parent_folder_id,
                    "sub_file_index": sub_file_index,
                    "file_id": "",
                    "client_version": "3.21.0",
                    "platform": "synology",
                    "source": "other"
                }
            }
            
            log.info("正在提交下载任务...")
            log.debug(f"任务参数: {task_params}")
            
            # 提交下载任务(带重试机制)
            MAX_RETRIES = 3
            retry_count = 0
            
            while retry_count < MAX_RETRIES:
                try:
                    result = self._make_request(
                        'POST',
                        "/drive/v1/task",
                        params={"device_space": ""},
                        json=task_params
                    )
                    
                    if result.get('HttpStatus') == 0 and result.get('task'):
                        task_id = result['task'].get('id')
                        task_phase = result['task'].get('phase')
                        log.info("-" * 50)
                        log.info("下载任务创建成功!")
                        log.info(f"任务ID: {task_id}")
                        log.info(f"任务状态: {task_phase}")
                        return task_id  # 返回任务ID
                    
                    retry_count += 1
                    log.warning(f"创建任务失败,第{retry_count}次重试...")
                    time.sleep(1)
                except Exception as e:
                    log.error(f"请求异常: {str(e)}")
                    retry_count += 1
                    
            log.error(f"创建下载任务失败,已重试{MAX_RETRIES}次: {result}")
            return False
            
        except Exception as e:
            log.error(f"添加下载任务异常: {str(e)}")
            ExceptionUtils.exception_traceback(e)
            return False

    def _download_magnet(self, magnet_link: str, download_dir=None):
        """
        下载磁力链接
        """
        try:
            # 取资源列表
            url = "/drive/v1/resource/list"
            params = {"device_space": ""}
            body = {"urls": magnet_link}
            
            log.info("正在获取资源列表...")
            data = self._make_request('POST', url, params=params, json=body, timeout=60)
            
            if not data.get('list', {}).get('resources'):
                log.error(f"未找到资源: {data}")
                return False
            
            task_name = data.get('list').get('resources')[0].get('name')
            log.info(f"获取到任务名称: {task_name}")
            
            # 检查任务是否已存在
            all_tasks = self.get_torrents()[0] or []
            all_task_names = [task.get('name') for task in all_tasks]
            if task_name in all_task_names:
                log.info(f"任务已存在，跳过: {task_name}")
                return True
            
            # 提交下载任务
            body = {
                "type": "user#download-url",
                "name": task_name,
                "file_name": task_name,
                "space": self._device_id,
                "params": {
                    "target": self._device_id,
                    "url": magnet_link,
                    "parent_folder_id": download_dir or self._folder_name
                }
            }
            
            result = self._make_request(
                'POST',
                "/drive/v1/task",
                params={"device_space": ""},
                json=body
            )
            
            if result.get('HttpStatus') == 0 and result.get('task'):
                task_id = result['task'].get('id')
                task_phase = result['task'].get('phase')
                log.info(f"任务创建成功: {task_name}")
                log.info(f"任务ID: {task_id}")
                log.info(f"任务状态: {task_phase}")
                return True
            else:
                log.error(f"任务创建失败: {result}")
                return False
            
        except Exception as e:
            log.error(f"下载磁力链接发生错误: {str(e)}")
            return False

    def delete_torrents(self, delete_file, ids):
        """
        删除种子
        :param delete_file: 是否删除文件
        :param ids: 种子ID列表
        :return: bool
        """
        try:
            if not self._get_auth() or not ids:
                return False
            
            if isinstance(ids, str):
                ids = [ids]
            
            # 添加更详细的日志
            log.info(f"准备删除任务:")
            log.info(f"传入的任务ID: {ids}")

            # 执行删除操作
            delete_result = self._make_request(
                'POST',
                "/method/patch/drive/v1/task",
                params={"device_space": "", "pan-auth": self._pan_auth},
                json={
                    "space": self._device_id,
                    "type": "user#download-url",
                    "id": ids[0],
                    "set_params": {
                        "spec": "{\"phase\":\"delete\"}"
                    }
                }
            )
            
            log.debug(f"删除请求结果: {delete_result}")
            if not delete_result:
                log.error("删除请求失败")
                return False
            
            # 任务不存在也视为删除成功
            if delete_result.get('error_code', 0) == 5:  # task_not_found
                log.warning(f"任务已经不存在: {ids[0]}")
                return True
            
            # 检查删除是否成功
            if delete_result.get('error_code', 0) == 0:
                log.info(f"成功删除任务: {ids}")
                return True
            
            log.error(f"删除任务失败: {delete_result}")
            return False
            
        except Exception as e:
            log.error(f"删除任务异常: {str(e)}")
            ExceptionUtils.exception_traceback(e)
            return False

    def start_torrents(self, ids):
        """
        开始/重新开始下载
        :param ids: 种子ID列表
        :return: dict
        """
        try:
            if not self._get_auth() or not ids:
                return False
            
            if not self._device_id:
                self._device_id = self.get_device_id()
            
            if isinstance(ids, str):
                ids = [ids]
            
            log.info(f"开始/重新开始任务ID: {ids}")
            
            # 获取当前任务状态
            params = {
                "device_space": "",
                "space": self._device_id,
                "limit": "100",
                "page_token": "",
                "filters": json.dumps({
                    "phase": {
                        "in": "PHASE_TYPE_ERROR,PHASE_TYPE_PAUSED"  # 同时获取错误和暂停状态的任务
                    },
                    "type": {
                        "in": "user#download-url,user#download"
                    }
                })
            }
            
            result = self._make_request(
                'GET',
                "/drive/v1/tasks",
                params=params
            )
            
            if not result or not result.get("tasks"):
                return []
            
            current_phase = None
            for task in result.get("tasks", []):
                if ids[0] in task.get("id"):
                    current_phase = task.get("status")
                    log.info(f"任务ID: {task.get('id')} 任务状态: {current_phase}")
                    break
            
            # 根据不同状态采取不同的处理策略
            if current_phase == "PHASE_TYPE_ERROR":
                log.info("检测到失败任务，执行重新下载流程...")
                # 对于失败的任务，需要先重置状态为pending
                reset_result = self._make_request(
                    'POST',
                    "/method/patch/drive/v1/task",
                    params={"device_space": "", "pan-auth": self._pan_auth},
                    json={
                        "space": self._device_id,
                        "type": "user#download-url",
                        "id": ids[0],
                        "set_params": {
                            "spec": "{\"phase\":\"pending\"}"
                        }
                    }
                )
                
                if not reset_result or reset_result.get('error_code', 0) != 0:
                    log.error("重置失败任务状态失败")
                    return False
                    
                log.info("失败任务重置状态成功，准备重新启动...")
                
            elif current_phase == "PHASE_TYPE_PAUSED":
                log.info("检测到暂停任务，直接执行启动...")
            else:
                log.info("任务状态正常，执行启动...")
            
            # 启动/重启任务
            result = self._make_request(
                'POST',
                "/method/patch/drive/v1/task",
                params={"device_space": "", "pan-auth": self._pan_auth},
                json={
                    "space": self._device_id,
                    "type": "user#download-url",
                    "id": ids[0],
                    "set_params": {
                        "spec": "{\"phase\":\"running\"}"
                    }
                }
            )
            
            if result and result.get('error_code', 0) == 0:
                status_desc = {
                    "PHASE_TYPE_ERROR": "重新启动",
                    "PHASE_TYPE_PAUSED": "恢复",
                }.get(current_phase, "启动")
                log.info(f"成功{status_desc}任务: {ids}")
                return {"id": ids[0]}
            
            log.error(f"任务启动失败: {result}")
            return False
            
        except Exception as e:
            log.error(f"启动任务异常: {str(e)}")
            ExceptionUtils.exception_traceback(e)
            return False

    def stop_torrents(self, ids):
        """
        停止下载
        :param ids: 种子ID列表
        :return: dict
        """
        try:
            if not self._get_auth() or not ids:
                return False
            
            if not self._device_id:
                self._device_id = self.get_device_id()
            
            if isinstance(ids, str):
                ids = [ids]
            
            log.info(f"暂停任务ID: {ids}")
            
            # 使用PATCH方法暂停任务
            result = self._make_request(
                'POST',
                "/method/patch/drive/v1/task",
                params={"device_space": "", "pan-auth": self._pan_auth},
                json={
                    "space": self._device_id,
                    "type": "user#download-url",
                    "id": ids[0],  # 使用第一个ID
                    "set_params": {
                        "spec": "{\"phase\":\"pause\"}"  # 设置暂停状态
                    }
                }
            )
            
            if result and result.get('error_code', 0) == 0:
                log.info(f"成功暂停任务: {ids}")
                return {"id": ids[0]}  # 返回字典格式，包含id字段
            
            log.error(f"暂停任务失败: {result}")
            return False
            
        except Exception as e:
            log.error(f"暂停任务异常: {str(e)}")
            ExceptionUtils.exception_traceback(e)
            return False

    def set_torrents_status(self, ids, tags=None):
        """
        设置种子状态
        :param ids: 种子ID列表
        :param tags: 标签
        :return: bool
        """
        try:
            # TODO: 实现设置下载任务状态
            return True
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            return False

    def get_download_dirs(self):
        """
        获取下载目录清单
        :return: list
        """
        try:
            # TODO: 实现获取下载目录列表
            return []
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            return []

    def change_torrent(self, tid, tag=None, category=None,
                      upload_limit=None, download_limit=None,
                      ratio_limit=None, seeding_time_limit=None):
        """
        修改种子
        :param tid: 种子ID
        :param tag: 标签
        :param category: 分类
        :param upload_limit: 上传限速
        :param download_limit: 下载限速
        :param ratio_limit: 分享率限制
        :param seeding_time_limit: 做种时限制
        :return: bool
        """
        try:
            # TODO: 实现修改下载任务
            return True
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            return False

    def get_transfer_task(self, tag=None, match_path=None):
        """
        获取需要转移的任务列表
        :param tag: 标签
        :param match_path: 匹配路径
        """
        try:
            # TODO: 实现获取需要转移的任务列表
            return []
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            return []

    def get_remove_torrents(self, config=None):
        """
        获取需要清理的种子清单
        :param config: 配置
        :return: list
        """
        try:
            # TODO: 实现获取需要清理的种子清单
            return []
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            return []

    def get_files(self, tid):
        """
        获取种子文件列表
        :param tid: 种子ID
        :return: list
        """
        try:
            # TODO: 实现获取种子文件列表
            return []
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            return []

    def set_files(self, **kwargs):
        """
        设置下载文件的状态
        """
        try:
            # TODO: 实现设置下载文件状态
            return True
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            return False

    def recheck_torrents(self, ids):
        """
        重新校验种子
        :param ids: 种子ID列表
        :return: bool
        """
        try:
            # TODO: 实现重新校验种子
            return True
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            return False

    def set_speed_limit(self, download_limit=None, upload_limit=None):
        """
        设置速度限制
        :param download_limit: 下载速度限制
        :param upload_limit: 上传速度限制
        :return: bool
        """
        try:
            # TODO: 实现设置速度限制
            return True
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            return False 

    def connect(self):
        """
        连接下载器
        """
        return True if self._get_auth() else False

    def get_downloading_progress(self, tag=None, ids=None):
        """
        获取正在下载的任务进度
        :return: 下载进度信息列表
        """
        if not self._get_auth():
            return []
        
        if not self._device_id:
            self._device_id = self.get_device_id()
            
        ret_array = []
        try:
            # 构造基础请求参数
            params = {
                "device_space": "",
                "space": self._device_id,
                "limit": "100",
                "page_token": "",
                "filters": json.dumps({
                    "phase": {
                        "in": "PHASE_TYPE_PENDING,PHASE_TYPE_RUNNING,PHASE_TYPE_PAUSED,PHASE_TYPE_ERROR"
                    },
                    "type": {
                        "in": "user#download-url,user#download"
                    }
                })
            }            
            # 调用迅雷API获取任务列表
            result = self._make_request(
                'GET',
                "/drive/v1/tasks",
                params=params
            )
            
            if not result or not result.get("tasks"):
                return []
            
            log.info(f"获取任务列表: {result.get('tasks')}")
            for task in result.get("tasks", []):
                # 获取任务参数
                params = task.get("params", {})
                phase = task.get("phase")
                
                # 检查是否为已标记删除的任务
                spec = params.get("spec", "{}")
                try:
                    spec_dict = json.loads(spec)
                    if spec_dict.get("phase") == "delete":
                        log.info(f"跳过已标记删除的任务: {task.get('id')}")
                        continue
                except json.JSONDecodeError:
                    pass  # spec 解析失败时继续处理
                
                # 添加日志记录任务ID和状态
                log.info(f"TaskID: {task.get('id')} 任务状态: {phase}")
                
                # 计算进度和大小
                file_size = int(task.get("file_size", 0))
                checked_size = int(params.get("checked_size", 0))
                
                # 计算进度百分比
                if file_size > 0:
                    progress = round((checked_size / file_size) * 100, 1)
                else:
                    progress = 0
                
                # 根据状态设置速度和显示文本
                speed = "0.0B/s"
                dlspeed = "0.0B/s"
                
                if phase == "PHASE_TYPE_RUNNING":
                    # 从 params 中获取速度值
                    raw_speed = params.get("speed")
                    if raw_speed:
                        try:
                            raw_speed = int(raw_speed)
                            speed_str = StringUtils.str_filesize(raw_speed) + "/s"
                            speed = speed_str
                            dlspeed = speed_str
                            log.debug(f"下载速度 - 原始值: {raw_speed} bytes/s, 显示值: {speed_str}")
                        except (ValueError, TypeError) as e:
                            log.error(f"速度值转换失败: {raw_speed}, error: {str(e)}")
                elif phase == "PHASE_TYPE_PAUSED":
                    speed = "已暂停"
                    dlspeed = "已暂停"
                elif phase == "PHASE_TYPE_ERROR":
                    speed = "下载失败"
                    dlspeed = "下载失败"
                
                # 构造显示信息
                task_info = {
                    "id": task.get("id"),                    
                    "title": task.get("file_name"),          
                    "name": task.get("file_name"),           
                    "size": file_size,                       
                    "progress": progress,  # 使用计算出的进度
                    "state": self._get_status(phase),                          
                    "speed": speed,
                    "dlspeed": dlspeed,
                    "upspeed": "0.0B/s",
                    "downloaded": checked_size,  # 已下载大小
                    "status": phase,
                    "save_path": params.get("real_path", ""),
                    "noprogress": phase in ["PHASE_TYPE_ERROR", "PHASE_TYPE_PAUSED"],
                    "nomenu": False,
                    "created_time": task.get("created_time"),
                    "updated_time": task.get("updated_time"),
                    "error": params.get("error", ""),
                    "retry_times": params.get("retry_times", "0"),
                    "speedup_status": params.get("speedup_status"),
                    "speedup": params.get("speedup", "{}"),
                    "url": params.get("url", ""),
                    "total_file_count": params.get("total_file_count", "0"),
                    "client_info": {
                        "version": params.get("client_version"),
                        "platform": params.get("platform"),
                        "device_model": params.get("device_model")
                    }
                }
                
                ret_array.append(task_info)
                
            return ret_array

        except Exception as e:
            log.error(f"【{self.client_name}】获取下载进度失败: {str(e)}")
            ExceptionUtils.exception_traceback(e)
            return []
        
    def _get_status(self, phase):
        """
        转换迅雷状态为通用状态
        """
        if not phase:
            return "unknown"
        
        # 迅雷状态映射到UI状态
        status_map = {
            "PHASE_TYPE_PENDING": "Downloading",   # 等待中显示为下载中
            "PHASE_TYPE_RUNNING": "Downloading",   # 下载中
            "PHASE_TYPE_COMPLETE": "Completed",    # 已完成
            "PHASE_TYPE_ERROR": "Stoped",         # 错误状态显示为已暂停，允许重试
            "PHASE_TYPE_PAUSED": "Stoped",        # 已暂停
            "PHASE_TYPE_RECYCLED": "Error"        # 已回收显示为错误
        }
        
        return status_map.get(phase, "unknown")

    def get_pan_auth(self):
        """
        获取 pan-auth token
        """
        return self._pan_auth 

    def get_device_id(self):
        """
        获取设备ID
        """
        try:
            result = self._make_request('POST', '/device/info/watch')
            if result and result.get("target"):
                device_id = result.get("target")
                log.info(f"获取设备ID: {device_id}")
                return device_id
            return None
        except Exception as e:
            log.error(f"获取设备ID失败: {str(e)}")
            return None 

    def _torrent2magnet(self, torrent_content):
        """
        种子文件转磁力链接
        :param torrent_content: 种子文件内容(bytes)
        :return: 磁力链接或None
        """
        try:
            log.info("开始转换种子文件为磁力链接...")
            
            # 确保有认证信息
            if not self._get_auth():
                log.error("获取认证失败")
                return None
            
            # 准备multipart/form-data格式的数据
            torrent_file = io.BytesIO(torrent_content)
            
            # 构造multipart表单数据
            m = MultipartEncoder(
                fields={
                    'file': ('file.torrent', torrent_file, 'multipart/form-data')
                }
            )
            log.info(f"Content-Type: {m.content_type}")
            
            # 发送请求 - 修改为正确的URL路径
            response = self._make_request(
                'POST',
                '/device/btinfo',  # 修改这里的URL路径
                params={'device_space': ''},
                data=m,
                headers={
                    'Content-Type': m.content_type,
                    'pan-auth': self._pan_auth
                }
            )
            
            if response and response.get('error') == 'ok' and response.get('url'):
                magnet_link = response.get('url')
                log.info(f"转换成功: {magnet_link}")
                return magnet_link
            
            log.error(f"转换失败: {response}")
            return None
            
        except Exception as e:
            log.error(f"转换种子文件异常: {str(e)}")
            ExceptionUtils.exception_traceback(e)
            return None

    def _get_folder_id(self, folder_path=None):
        """
        获取下载目录ID,如果目录不存在则返回None
        :param folder_path: 完整的目录路径如 /downloads/源文件-电视
        :return: 目录ID 或 None(目录不存在时)
        """
        try:
            log.info("-" * 50)
            log.info("开始查找下载目录...")
            log.info(f"目标路径: {folder_path}")
            
            # 使用默认目录或传入的目录
            folder_path = folder_path or self._folder_name
            if not folder_path:
                log.error("未提供有效的下载目录路径")
                return None
                
            # 检查缓存
            current_time = time.time()
            if folder_path in self._folder_id_cache:
                cache_time = self._folder_id_cache_time.get(folder_path, 0)
                if current_time - cache_time < self._cache_expire_time:
                    folder_id = self._folder_id_cache[folder_path]
                    log.info(f"从缓存获取目录ID: {folder_id}")
                    return folder_id
            
            # 移除开头和结尾的斜杠并分割路径
            folder_parts = folder_path.strip('/').split('/')
            log.info(f"目录层级解析: {' -> '.join(folder_parts)}")
            
            # 保存每一级的目录ID和路径映射
            path_id_map = {"": ""}  # 根目录映射
            current_path = ""
            current_parent_id = ""
            
            # 逐级检查每层目录
            for index, folder_part in enumerate(folder_parts, 1):
                if not folder_part:
                    continue
                    
                # 构建当前完整路径
                current_path = f"{current_path}/{folder_part}".lstrip('/')
                log.info(f"正在查找第 {index} 级目录: {folder_part}")
                log.info(f"当前父目录ID: {current_parent_id or '根目录'}")
                
                # 获取当前级的目录列表
                result = self._make_request(
                    'GET',
                    "/drive/v1/files",
                    params={
                        "space": self._device_id,
                        "limit": "200",
                        "parent_id": current_parent_id,
                        "filters": "{\"kind\":{\"eq\":\"drive#folder\"}}",
                        "page_token": "",
                        "device_space": ""
                    }
                )
                
                if not result:
                    log.error(f"获取目录列表失败: {folder_part}")
                    return None
                    
                current_folder_id = None
                
                # 在当前层级查找文件夹
                if result.get('kind') == 'drive#fileList':
                    files = result.get('files', [])
                    
                    # 记录当前层级的所有目录名称和ID
                    folder_info = {
                        file.get('name'): file.get('id') 
                        for file in files 
                        if file.get('kind') == 'drive#folder'
                    }
                    log.debug(f"当前层级目录列表: {list(folder_info.keys())}")
                    
                    # 查找匹配目录ID
                    current_folder_id = folder_info.get(folder_part)
                    if current_folder_id:
                        log.info(f"✓ 找到目录: {folder_part}")
                        log.info(f"  目录ID: {current_folder_id}")
                        # 保存当前层级的路径和ID映射
                        path_id_map[current_path] = current_folder_id
                    
                # 如果任意层级目录不存在则返回None
                if not current_folder_id:
                    log.warning(f"✗ 目录不存在: {folder_part}")
                    log.warning(f"  查找路径: {' -> '.join(folder_parts[:index])}")
                    return None
                
                # 更新父目录ID当前目录ID
                current_parent_id = current_folder_id
            
            # 更新缓存
            for path, folder_id in path_id_map.items():
                if path:  # 不缓存根目录
                    self._folder_id_cache[path] = folder_id
                    self._folder_id_cache_time[path] = current_time
            
            log.info("-" * 50)
            log.info("目录查找完成!")
            log.info(f"目标路径: {folder_path}")
            log.info(f"目录ID: {current_folder_id}")
            log.info(f"缓存路径数: {len(path_id_map) - 1}")  # 去根目录
            log.info("-" * 50)
            return current_folder_id
            
        except Exception as e:
            log.error("-" * 50)
            log.error(f"查找目录结构异常:")
            log.error(f"目标路径: {folder_path}")
            log.error(f"错误信息: {str(e)}")
            log.error("-" * 50)
            ExceptionUtils.exception_traceback(e)
            return None 

    def clear_folder_cache(self):
        """
        清除目录ID缓存
        """
        self._folder_id_cache.clear()
        self._folder_id_cache_time.clear() 