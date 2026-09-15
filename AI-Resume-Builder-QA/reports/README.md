<!-- author: jf -->
# 测试报告

pytest 默认把 JUnit XML 写入 `reports/junit/pytest.xml`，临时测试数据写入 `reports/runtime/pytest-temp/`。生成物由 `.gitignore` 排除，执行记录只保留脱敏状态、数量与阻塞原因。
