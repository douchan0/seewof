# 前端静态资源

为简化部署 (无 npm 构建), 这里使用 CDN 预编译版本.

**首次部署需手动下载以下文件到本目录**:

| 文件名 | 来源 |
|--------|------|
| `vue.global.prod.js` | https://unpkg.com/vue@3.5.13/dist/vue.global.prod.js |
| `element-plus.css` | https://unpkg.com/element-plus@2.8.6/dist/index.css (重命名为 element-plus.css) |
| `element-plus.full.min.js` | https://unpkg.com/element-plus@2.8.6/dist/index.full.min.js (重命名为 element-plus.full.min.js) |
| `axios.min.js` | https://unpkg.com/axios@1.7.7/dist/axios.min.js (重命名为 axios.min.js) |

**Linux 自动下载脚本**:

```bash
cd server/web/assets
curl -L -o vue.global.prod.js https://unpkg.com/vue@3.5.13/dist/vue.global.prod.js
curl -L -o element-plus.css https://unpkg.com/element-plus@2.8.6/dist/index.css
curl -L -o element-plus.full.min.js https://unpkg.com/element-plus@2.8.6/dist/index.full.min.js
curl -L -o axios.min.js https://unpkg.com/axios@1.7.7/dist/axios.min.js
```

如果服务器没有公网, 提前在有网机器下载好 scp 上去即可.
