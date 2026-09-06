<!-- author: jf -->
# 测试数据

第一阶段的 Markdown、PDF、DOCX 和图片均由 Fixture 在 `reports/runtime/pytest-temp/` 动态生成。内容只包含随机测试标记与合成图片，不使用真实简历。运行结束后 pytest 会清理临时目录；知识库清理器只删除本轮登记且文件名通过安全核对的数据。
