# Office

## 项目简介
五件套办公工具集合，包含动画思维导图、乱文本转MD、AI字处理工具自动插图、任意文档网页转MD、分解项目转md文档等功能。

## 功能列表
- 动画思维导图（可当幻灯片演示）
- 乱文本转Markdown
- AI字处理工具自动插图
- 任意文档网页转Markdown
- 分解项目转Markdown文档

## 目录结构
```
plugins/applications/office/
  ├── app.yaml
  ├── _init.py
  ├── converter.py
  ├── folder_scan.py
  ├── generate.py
  ├── processor.py
  ├── text_to_md.py
  ├── upload.py
  ├── web_converter.py
  └── README.md

www/office/
  ├── AICP-Mind.html
  ├── mindmap.css
  ├── mindmap.js
  ├── office2md.html
  ├── project2md.html
  ├── text-processor.html
  └── text_to_md.html
```

## API 接口说明
| action | 说明 | 必填参数 | 路径 |
|--------|------|----------|------|
| init | 初始化动作 | - | plugins/applications/office/_init.py |

## 前端页面说明
| 页面路径 | 说明 |
|----------|------|
| www/office/AICP-Mind.html | 动画思维导图页面 |
| www/office/office2md.html | 文档转Markdown页面 |
| www/office/project2md.html | 项目转Markdown页面 |
| www/office/text-processor.html | AI字处理工具页面 |
| www/office/text_to_md.html | 文本转Markdown页面 |

## 配置说明
无特殊配置项。

## 前端调用示例
```javascript
var project = window.location.pathname.split('/')[1];
var API = '/api/applications/' + project;
var PLUGIN = 'office_api';
async function request(payload) {
    var resp = await fetch(API + '/' + PLUGIN, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({payload: payload})
    });
    var json = await resp.json();
    var result = json.data || json;
    result.ok = json.ok;
    return result;
}

// 初始化调用示例
async function initOffice() {
    var payload = {
        action: 'init'
    };
    return await request(payload);
}
```

## 已知注意事项
- 请求body必须使用`payload`字段嵌套
- 依赖Python库：python-docx, openpyxl, python-pptx, requests, beautifulsoup4, markdownify, PyMuPDF, pdfplumber