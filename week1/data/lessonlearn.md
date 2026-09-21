- 非推理型模型处理字符能力有缺陷,一些推理模型(reasoning models)会在内部生成思维链
  - 使用 K-Shot 模式（关键）
    也就是在 System Prompt 中提供多个“输入 -> 输出”的示例，让模型通过模式匹配来完成任务。

    即使做了以上所有优化,mistral-nemo:12b 在这个任务上的成功率仍然不会是 100%。

  - 为什么无法反转httpstatus

  - 结论是：问题大概率不在你的提示词上，而在于 httpstatus 这个测试词恰好命中了 mistral-nemo:12b 最不擅长的字符级反转任务，且现有提示词的“控制力”不足以覆盖这个特定词的复杂结构

  - 为什么偏偏是 httpstatus 不行？
        httpstatus 对模型来说是一个“坏样本”，它同时包含：
        重复字符：t 出现了 3 次，s 出现了 2 次。
        常见 token 前缀：http 和 status 都是高频词，极易被模型当作一个整体来处理。

  - httpstatus 的核心难点在于：多个重复字母 + 高频子词。下面这些词和它“同构”，适合作为测试用例或 few-shot 示例：
        httpserver	revresptth	http 前缀 + 重复 t、e、r
        statustext	txetsutats	status 子词 + 重复 t、s
        teststatus	sutats tset → sutatstset	重复 t、s，两个高频子词
        stresstest	tsets serts → tsetsserts	重复 s、t，stress + test
        httpsstatus	sutatsptth	双 t、双 s，http + status
        statistics	scitsitats	重复 t、s、i
        assessment	tnemssessa	重复 s，双 s 连续
        successful	lufsseccus	重复 s、c，连续 ss