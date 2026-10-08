"""把 unittest 的失败和报错转成 GitHub Actions 的注解。

Actions 的完整日志要登录才能看，注解在运行概览页上谁都能看到。打包机上的
测试挂了，不用登录也能知道是哪一条、报了什么。
"""
import re
import sys

MAX_ANNOTATIONS = 10
MAX_BODY = 3000
HEADER = re.compile(r'^(ERROR|FAIL): (.+)$')


def failures(text):
    """按 unittest 的分隔线切出每一条失败：[(标题, 正文), ...]。"""
    blocks = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith('=' * 20) and i + 1 < len(lines):
            match = HEADER.match(lines[i + 1])
            if match:
                body = []
                j = i + 2
                if j < len(lines) and lines[j].startswith('-' * 20):
                    j += 1
                while j < len(lines) and not lines[j].startswith('=' * 20) \
                        and not (lines[j].startswith('-' * 20) and j + 1 < len(lines)
                                 and lines[j + 1].startswith('Ran ')):
                    body.append(lines[j])
                    j += 1
                blocks.append((f'{match.group(1)}: {match.group(2)}', '\n'.join(body).strip()))
                i = j
                continue
        i += 1
    return blocks


def escape(value, property_value=False):
    value = value.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    if property_value:
        value = value.replace(':', '%3A').replace(',', '%2C')
    return value


def annotations(text):
    result = []
    for title, body in failures(text)[:MAX_ANNOTATIONS]:
        if len(body) > MAX_BODY:
            # 留尾巴：真正出错的那一帧和异常信息在最后
            body = '…\n' + body[-MAX_BODY:]
        result.append(f'::error title={escape(title, True)}::{escape(body)}')
    return result


def main(path):
    with open(path, encoding='utf-8', errors='replace') as handle:
        text = handle.read()
    lines = annotations(text)
    for line in lines:
        print(line)
    if not lines:
        # 没解析出来（比如进程直接崩了），至少把最后一段输出亮出来
        print('::error title=unittest::' + escape(text[-MAX_BODY:]))


if __name__ == '__main__':
    main(sys.argv[1])
