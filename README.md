基于[hsuyelin/nas-tools](https://github.com/hsuyelin/nas-tools) 二次开发, 加入迅雷下载器
## 必要安装
[Docker迅雷](https://github.com/cnk3x/xunlei)

安装Nastool:

Docker安装nastool: docker pull dominique9898/nastool:latest-alpine, 自行添加自己配置的port映射和地址映射
登录Nastool local_nas_ip:port
默认登录账号密码为, username: admin, password: password.
在下载器配置迅雷

XL_GID和XL_UID 可以通过`id -u` 查看.


## Docker安装该版本
`docker pull dominique9898/nastool:latest-alpine`
内测码(暂时): 迅雷牛通
支持Nastool和迅雷页面同步下载速度速度,下载状态和下载操作

<img width="1492" height="1562" alt="image" src="https://github.com/user-attachments/assets/1cfb9ca7-2ecb-4480-9ec5-df9a09bb9471" />
<img width="1482" height="1114" alt="image" src="https://github.com/user-attachments/assets/dde1e4c2-d632-45e0-a3f3-21fe4caafa48" />
