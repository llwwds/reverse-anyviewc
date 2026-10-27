#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AnyView 实验课（数据结构实验，scheme 466）——多文件工程的抓取 / 编译 / 运行 / 保存。

实验课跟理论课是**两套完全独立的子系统**，别混：

  理论课 scheme 465   questionType=1  单文件
    /scheme-service/scheme/list/student/{eid}         题干 + stuCode(base64 单串)
    /gdb-service/code/runGroup                        判题，写 exerciseStatus=2

  实验课 scheme 466   questionType=3  多文件工程（SqStack.h/.cpp + Main.cpp）
    /scheme-service/scheme/list/student/lab/{eid}     工程文件 + completes + 三栏富文本
    /gdb-service/code/lab/compile                     JSON，编译
    /gdb-service/code/lab/run                         JSON，运行判题（返回 passed）
    /gdb-service/exercise/lab/saveCode                JSON，保存工程
    /exam-service/lab/practice/get/question/466/{qid} 每个实验挂 8 道子题
    /exam-service/lab/practice/get/exercise/{eid}    自己的作答记录
    /exam-service/lab/practice/submit                 提交子题

实验题的「状态」不进 catalog/list（返回体里根本没有 pass 字段），
理论课才有。所以实验课的完成度只能看 lab/practice 子题 + run 的 passed。

实验课还有两个恒为假的字段，别当判据：
  run 返回的 passed        无参考答案，恒 false
  子题 score / isCorrect   教师未批改前恒 0，选项与 optionSolution 完全一致也一样

用法:
  python lab.py dump                 # 把 21 个工程抓到 local-data/实验答案/<eid>/
  python lab.py dump --eid 141443
  python lab.py compile 141443       # 编译（读本地目录）
  python lab.py run 141443           # 运行判题，打印 output / passed
  python lab.py save 141443          # 保存到平台并回读校验
  python lab.py practice 141443      # 打印子题与自己的作答状态
  python lab.py submit-practice 141443  # 提交 _answers.json 里的子题作答
  python lab.py check                # 课程/目录侧的完成度总览
"""
import argparse
import base64
import json
import re
import sys
from html import unescape
from pathlib import Path

import paths
import submit as S


# ---------------------------------------------------------------- 数据根

def lab_root() -> Path:
    p = paths.data_root() / "实验答案"
    p.mkdir(parents=True, exist_ok=True)
    return p


def lab_dir(eid) -> Path:
    p = lab_root() / str(eid)
    p.mkdir(parents=True, exist_ok=True)
    return p


def lab_eids():
    """题库里 course 以「数据结构实验」开头的 21 条。"""
    q = json.loads((paths.data_root() / "题库" / "题库.json").read_text(encoding="utf-8"))
    return [str(x["eid"]) for x in q if str(x.get("course", "")).startswith("数据结构实验")]


# ---------------------------------------------------------------- 平台

def get_detail(tk, eid):
    st, j = S.call("GET", f"/scheme-service/scheme/list/student/lab/{eid}", tk)
    d = j.get("data")
    if not isinstance(d, dict):
        sys.exit(f"取实验失败 eid={eid} HTTP={st} code={j.get('code')} msg={j.get('message')}")
    return d


def get_practice(tk, eid, question_id):
    st, j = S.call(
        "GET", f"/exam-service/lab/practice/get/question/466/{question_id}", tk)
    return j.get("data") or []


def code_infos_from_dir(d):
    out = []
    for p in sorted(Path(d).iterdir()):
        if not p.is_file() or p.name.startswith("_"):
            continue
        out.append({"path": p.name,
                    "codeContent": base64.b64encode(p.read_bytes()).decode(),
                    "breakPoints": ""})
    return out


def code_infos_from_server(detail):
    return [{"path": f["filename"], "codeContent": f["fileValue"], "breakPoints": ""}
            for f in detail.get("stuCode") or []]


def post(tk, path, body):
    st, j = S.call("POST", path, tk, json_body=body)
    return st, j


def build_body(detail, code_infos, is_debug=False):
    # labExerciseId 必须等于题目的 eid（不是 questionId）。传错时后端回 404
    # 「Not Found /code/lab/compile」，看起来像路由不存在，其实是参数不匹配 ——
    # 跟理论课三个写接口那个 404 陷阱是同一类。
    return {"questionFullName": detail["questionFullName"],
            "labExerciseId": int(detail["eid"]),
            "codeInfos": code_infos,
            "isTeacher": False,
            "isDebug": is_debug}


# ---------------------------------------------------------------- 命令

def cmd_dump(args):
    tk = paths.require_token()
    eids = args.eid.split(",") if args.eid else lab_eids()
    for eid in eids:
        det = get_detail(tk, eid)
        d = lab_dir(eid)
        files = {}
        for f in det.get("stuCode") or []:
            body = base64.b64decode(f["fileValue"])
            (d / f["filename"]).write_bytes(body)
            files[f["filename"]] = len(body)
        prac = get_practice(tk, eid, det["questionId"])
        (d / "_meta.json").write_text(json.dumps({
            "eid": int(eid),
            "questionId": det["questionId"],
            "questionFullName": det["questionFullName"],
            "language": det.get("language"),
            "completes": det.get("completes"),
            "beforeClass": det.get("beforeClass"),
            "onClass": det.get("onClass"),
            "afterClass": det.get("afterClass"),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        (d / "_practice.json").write_text(
            json.dumps(prac, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{eid} {det['questionFullName']:20s} 文件 {len(files)} 个 {files} 子题 {len(prac)}")


def _local_detail(eid):
    meta = json.loads((lab_dir(eid) / "_meta.json").read_text(encoding="utf-8"))
    return meta


def cmd_compile(args):
    tk = paths.require_token()
    detail = _local_detail(args.eid)
    infos = code_infos_from_dir(lab_dir(args.eid))
    st, j = post(tk, "/gdb-service/code/lab/compile", build_body(detail, infos))
    res = (j.get("data") or {}).get("result", json.dumps(j, ensure_ascii=False))
    ok = "编译成功" in res
    print(("OK  " if ok else "FAIL") + f"  {args.eid}  " + _plain(res)[:600])
    return 0 if ok else 1


def cmd_run(args):
    tk = paths.require_token()
    detail = _local_detail(args.eid)
    infos = code_infos_from_dir(lab_dir(args.eid))
    st, j = post(tk, "/gdb-service/code/lab/run", build_body(detail, infos, args.debug))
    d = j.get("data") or {}
    print(f"passed={d.get('passed')} end={d.get('end')} exception={_plain(d.get('exception') or '')[:200]}")
    print("output:", _plain(d.get("output") or "")[:1500])


def cmd_save(args):
    tk = paths.require_token()
    detail = _local_detail(args.eid)
    infos = code_infos_from_dir(lab_dir(args.eid))
    st, j = post(tk, "/gdb-service/exercise/lab/saveCode",
                 {"codeInfo": infos, "labExerciseId": int(args.eid)})
    print("saveCode HTTP", st, json.dumps(j, ensure_ascii=False)[:300])
    back = get_detail(tk, args.eid)
    got = {f["filename"]: f["fileValue"] for f in back.get("stuCode") or []}
    same = all(got.get(i["path"]) == i["codeContent"] for i in infos)
    print("回读一致:", same, "文件:",
          {f["filename"]: f.get("lastUpdateDate") for f in back.get("stuCode") or []})
    return 0 if same else 1


def cmd_practice(args):
    tk = paths.require_token()
    detail = _local_detail(args.eid)
    prac = get_practice(tk, args.eid, detail["questionId"])
    done = submissions(tk, args.eid)
    for q in prac:
        p = q["pq"]
        rec = done.get(q["practiceQuestionId"], {})
        print("=" * 72)
        print(f"order={q['order']} pqId={q['practiceQuestionId']} {q['point']}分 "
              f"type={p['type']} {p['remark']} {p['name']} "
              f"| 已答={bool(rec.get('exerciseContent'))} 分={rec.get('score')} 对={rec.get('isCorrect')}")
        print("题干:", _plain(p["questionStem"])[:400])
        for o in p.get("optionSelects") or []:
            print(f"  [{o['order']}]", _plain(o["content"])[:200])


def submissions(tk, eid):
    """{practiceQuestionId: pqe 记录}。作答内容在 record["pqe"] 里，不在顶层。"""
    d = S.call("GET", f"/exam-service/lab/practice/get/exercise/{eid}", tk)[1].get("data") or []
    return {r["practiceQuestionId"]: (r.get("pqe") or {}) for r in d}


def cmd_practice_submit(args):
    """把 _answers.json 里的作答逐条提交。跳过已有非暂存记录的题，除非 --force。

    _answers.json 形如 {"1011": "[true,true,false]", "1012": "解答全文"}：
    选择题是 JSON 数组字面量，解答/代码是纯文本；两者都整体 base64 后放进
    exerciseContent，后端不做二次解析。
    """
    tk = paths.require_token()
    d = lab_dir(args.eid)
    ans = json.loads((d / "_answers.json").read_text(encoding="utf-8"))
    prac = json.loads((d / "_practice.json").read_text(encoding="utf-8"))
    done = submissions(tk, args.eid)
    ok = skip = fail = 0
    for q in prac:
        pid = str(q["practiceQuestionId"])
        if pid not in ans:
            print(f"SKIP {pid} 无答案")
            skip += 1
            continue
        rec = done.get(q["practiceQuestionId"]) or {}
        if rec.get("exerciseContent") and not rec.get("isTempSaved") and not args.force:
            print(f"SKIP {pid} 已有提交 {rec.get('submitTime')}")
            skip += 1
            continue
        body = {"practiceQuestionId": int(pid),
                "exerciseContent": base64.b64encode(ans[pid].encode()).decode(),
                "isTempSaved": False}
        st, j = post(tk, "/exam-service/lab/practice/submit", body)
        # 业务码不保证是 200（scheme/list 就回 31），所以单次判定只看「HTTP 200 且无 error」，
        # 真正的判据是函数末尾那次回读 —— 有一道题没落库就整体算失败。
        good = st == 200 and not j.get("error")
        print(("OK  " if good else "FAIL") + f" {pid} HTTP={st} code={j.get('code')} {str(j.get('message') or '')[:60]}")
        ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
    print(f"提交 {ok} 跳过 {skip} 失败 {fail}")
    # 回读核对：逐题确认平台上确实有非暂存记录
    back = submissions(tk, args.eid)
    miss = [q["practiceQuestionId"] for q in prac
            if not (back.get(q["practiceQuestionId"]) or {}).get("exerciseContent")]
    print("回读缺失:", miss or "无")
    return 0 if not fail and not miss else 1


def cmd_check(args):
    tk = paths.require_token()
    st, j = S.call("GET", "/scheme-service/scheme/list/student", tk)
    for s in j.get("data") or []:
        print(f"scheme {s['id']} {s['courseName']}: passNum={s.get('passNum')} / {s.get('totalExerciseNum')}")
    st, j = S.call("GET", "/scheme-service/catalog/list?schemeId=466&classId=340", tk)
    items = j.get("data") or []
    print("实验目录项字段:", sorted(items[0].keys()) if items else "无")
    print("带 pass 字段的项:", sum(1 for x in items if "pass" in x))
    if args.deep:
        for x in items:
            mine = S.call("GET", f"/exam-service/lab/practice/get/exercise/{x['eid']}", tk)[1].get("data") or []
            n_done = sum(1 for r in mine if not r["pqe"].get("isTempSaved"))
            score = sum(r["pqe"].get("score") or 0 for r in mine)
            print(f"  {x['eid']} {x['pname']:10s} 子题 {len(mine)} 已提交 {n_done} 得分 {score}")


def _plain(h):
    h = re.sub(r"<br\s*/?>", "\n", h or "")
    h = re.sub(r"<[^>]+>", "", h)
    return unescape(h).replace("\xa0", " ").strip()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("dump"); p.add_argument("--eid"); p.set_defaults(fn=cmd_dump)
    for name, fn in (("compile", cmd_compile), ("run", cmd_run), ("save", cmd_save),
                     ("practice", cmd_practice)):
        p = sub.add_parser(name)
        p.add_argument("eid")
        if name == "run":
            p.add_argument("--debug", action="store_true")
        p.set_defaults(fn=fn)
    p = sub.add_parser("check"); p.add_argument("--deep", action="store_true"); p.set_defaults(fn=cmd_check)
    p = sub.add_parser("submit-practice")
    p.add_argument("eid")
    p.add_argument("--force", action="store_true", help="已有提交也重交")
    p.set_defaults(fn=cmd_practice_submit)

    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
