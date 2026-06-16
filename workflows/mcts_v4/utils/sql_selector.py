"""
SQL选择策略

包含不同的SQL选择策略
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Tuple

STRATEGY_ENV = "MCTS_SELECTOR_STRATEGY"
SCORE_MODE_ENV = "MCTS_R4_SCORE_MODE"
WITH_BIAS_ENV = "MCTS_R4_WITH_BIAS"
WITH_BIAS_MARGIN_ENV = "MCTS_R4_WITH_BIAS_MARGIN"
TOPK_BOOTSTRAP_ENV = "MCTS_R4_TOPK_BOOTSTRAP"
TOPK_ENV = "MCTS_R4_TOPK"

_STRATEGY_ALIASES = {
    "R0": "R0",
    "R0_MAX_REWARD": "R0",
    "R0_max_reward": "R0",
    "max_reward": "R0",
    "R2": "R2",
    "R2_MAX_CLUSTER_VISIT": "R2",
    "R2_max_cluster_visit": "R2",
    "R3": "R3",
    "R3_REWARD_X_SIZE": "R3",
    "R3_reward_x_size": "R3",
    "R4": "R4",
    "R4_MAJORITY_THEN_REWARD": "R4",
    "R4_majority_then_reward": "R4",
}


@dataclass
class _Cluster:
    sig: str
    total_count: int = 0
    total_visit: int = 0
    variants: List[Tuple[str, float, int]] = field(default_factory=list)

    @property
    def max_rollout_reward(self) -> float:
        return max((v[1] for v in self.variants), default=0.0)


class SQLSelector:
    """SQL选择策略工具类"""

    @staticmethod
    def _r4_score_mode() -> str:
        return os.environ.get(SCORE_MODE_ENV, "votes").strip().lower()

    @staticmethod
    def _with_bias_enabled() -> bool:
        return os.environ.get(WITH_BIAS_ENV, "0").strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _with_bias_margin() -> float:
        raw = os.environ.get(WITH_BIAS_MARGIN_ENV, "1.5").strip()
        try:
            return float(raw)
        except ValueError:
            return 1.5

    @staticmethod
    def _topk_bootstrap_enabled() -> bool:
        raw = os.environ.get(TOPK_BOOTSTRAP_ENV, "0").strip().lower()
        return raw not in ("0", "false", "no", "off", "")

    @staticmethod
    def _topk_bootstrap_mode() -> str:
        return os.environ.get(TOPK_BOOTSTRAP_ENV, "0").strip().lower()

    @staticmethod
    def _topk_clusters() -> int:
        raw = os.environ.get(TOPK_ENV, "3").strip()
        try:
            return max(2, int(raw))
        except ValueError:
            return 3

    @staticmethod
    def _norm_sql(sql: str) -> str:
        return " ".join((sql or "").split()).strip().lower()

    @staticmethod
    def _maybe_final_jaccard_rollouts(
        rollout_stats_list: List[Dict[str, Any]],
        *,
        db_connector=None,
    ) -> List[Dict[str, Any]]:
        from .cluster_merge import (
            final_jaccard_enabled,
            jaccard,
            jaccard_threshold,
            remap_rollout_signatures_by_jaccard,
            rowset_from_execution,
        )

        if not final_jaccard_enabled() or not db_connector:
            return rollout_stats_list

        tau = jaccard_threshold("MCTS_FINAL_JACCARD_THRESHOLD", 0.85)
        seen: Dict[str, str] = {}
        sql_list: List[str] = []
        rowsets: List[Any] = []

        for rs in rollout_stats_list or []:
            for v in rs.get("all_sql_variants") or []:
                sql = (v.get("sql") or "").strip()
                if not sql or not v.get("valid"):
                    continue
                nk = SQLSelector._norm_sql(sql)
                if nk in seen:
                    continue
                seen[nk] = sql
                sql_list.append(sql)
                try:
                    df, err = db_connector.execute_query(sql)
                    if err or df is None:
                        rowsets.append(None)
                    else:
                        res = {"valid": True, "query_result": df}
                        rowsets.append(rowset_from_execution(res))
                except Exception:
                    rowsets.append(None)

        if len(sql_list) < 2:
            return rollout_stats_list

        n = len(sql_list)
        edges: List[Tuple[int, int]] = []
        for i in range(n):
            if rowsets[i] is None:
                continue
            for j in range(i + 1, n):
                if rowsets[j] is None:
                    continue
                if jaccard(rowsets[i], rowsets[j]) >= tau:
                    edges.append((i, j))

        if not edges:
            return rollout_stats_list

        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i, j in edges:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[rj] = ri

        sql_to_super = {
            SQLSelector._norm_sql(sql_list[i]): f"super_{find(i)}" for i in range(n)
        }
        print(f"[Final Jaccard] tau>={tau:.2f} remap {len(sql_list)} SQLs → super-clusters")
        return remap_rollout_signatures_by_jaccard(rollout_stats_list, sql_to_super)

    @staticmethod
    def _mul_purity_pick(
        rollout_stats_list: List[Dict[str, Any]],
        clusters: Dict[str, _Cluster],
        votes,
        *,
        db_connector=None,
    ) -> str:
        from collections import Counter

        best_sig, best_score = "", -1.0
        for sig, v in votes.items():
            c = clusters.get(sig)
            if not c:
                score = float(v)
            else:
                v2c: Counter = Counter()
                for variant in c.variants:
                    sql = variant[0]
                    v2 = ""
                    for rs in rollout_stats_list or []:
                        for item in rs.get("all_sql_variants") or []:
                            if SQLSelector._norm_sql(item.get("sql", "")) == SQLSelector._norm_sql(sql):
                                v2 = (item.get("result_signature_v2") or "").strip()
                                break
                        if v2:
                            break
                    if v2:
                        v2c[v2] += 1
                purity = max(v2c.values()) / max(sum(v2c.values()), 1) if v2c else 1.0
                score = float(v) * purity
            if score > best_score:
                best_score, best_sig = score, sig
        print(f"[Selection] R4 mul_purity: sig={best_sig[:16]}… score={best_score:.2f}")
        c = clusters.get(best_sig)
        if not c:
            return ""
        return SQLSelector._tiebreak_pick(c.variants, db_connector=db_connector)

    @staticmethod
    def _sig_v2_purity(
        rollout_stats_list: List[Dict[str, Any]],
        sig: str,
        clusters: Dict[str, _Cluster],
    ) -> float:
        from collections import Counter

        c = clusters.get(sig)
        if not c:
            return 1.0
        v2c: Counter = Counter()
        for variant in c.variants:
            sql = variant[0]
            v2 = ""
            for rs in rollout_stats_list or []:
                for item in rs.get("all_sql_variants") or []:
                    if SQLSelector._norm_sql(item.get("sql", "")) == SQLSelector._norm_sql(sql):
                        v2 = (item.get("result_signature_v2") or "").strip()
                        break
                if v2:
                    break
            if v2:
                v2c[v2] += 1
        if not v2c:
            return 1.0
        return max(v2c.values()) / max(sum(v2c.values()), 1)

    @staticmethod
    def _ranked_r4_cluster_sigs(
        rollout_stats_list: List[Dict[str, Any]],
        clusters: Dict[str, _Cluster],
        votes,
    ) -> List[str]:
        scored: List[Tuple[str, float, int]] = []
        for sig, v in votes.items():
            purity = (
                SQLSelector._sig_v2_purity(rollout_stats_list, sig, clusters)
                if SQLSelector._r4_score_mode() == "mul_purity"
                else 1.0
            )
            c = clusters.get(sig)
            reward = c.max_rollout_reward if c else 0.0
            scored.append((sig, float(v) * purity, int(v)))
        scored.sort(key=lambda x: (-x[1], -x[2], -clusters[x[0]].max_rollout_reward if x[0] in clusters else 0))
        return [s for s, _, _ in scored]

    @staticmethod
    def _sqls_for_cluster_sigs(clusters: Dict[str, _Cluster], sigs: List[str]) -> List[str]:
        seen: set = set()
        out: List[str] = []
        for sig in sigs:
            c = clusters.get(sig)
            if not c:
                continue
            for sql, _, _ in c.variants:
                s = (sql or "").strip()
                if s and s not in seen:
                    seen.add(s)
                    out.append(s)
        return out

    @staticmethod
    def _pick_r4_cluster_sql(
        rollout_stats_list: List[Dict[str, Any]],
        *,
        db_connector=None,
    ) -> str:
        """R4 cluster pick only (mul_purity / votes / with_bias); no top-K bootstrap."""
        rss = SQLSelector._maybe_final_jaccard_rollouts(
            rollout_stats_list, db_connector=db_connector
        )
        return SQLSelector._pick_r4_cluster_sql_from_rss(rss, db_connector=db_connector)

    @staticmethod
    def _pick_r4_cluster_sql_from_rss(
        rss: List[Dict[str, Any]],
        *,
        db_connector=None,
    ) -> str:
        clusters = SQLSelector._build_clusters(rss)
        if not clusters:
            return SQLSelector.select_by_highest_reward(rss)

        from .r4_vote import collect_r4_cluster_votes

        votes = collect_r4_cluster_votes(rss)
        if not votes:
            return SQLSelector.select_by_highest_reward(rss)

        if SQLSelector._r4_score_mode() == "mul_purity":
            return SQLSelector._mul_purity_pick(
                rss, clusters, votes, db_connector=db_connector
            )
        if SQLSelector._with_bias_enabled():
            return SQLSelector._struct_with_bias_pick(
                rss, clusters, votes, db_connector=db_connector
            )

        top_v = votes.most_common(1)[0][1]
        tied = [sig for sig, v in votes.items() if v == top_v]
        top_sig = tied[0][:16] if tied else "?"
        print(
            f"[Selection] R4: majority cluster sig={top_sig}… votes={top_v}"
        )
        if len(tied) == 1:
            return SQLSelector._tiebreak_pick(clusters[tied[0]].variants, db_connector=db_connector)

        best_r, best_sql = -1.0, ""
        for sig in tied:
            c = clusters.get(sig)
            if not c:
                continue
            if c.max_rollout_reward > best_r:
                best_r = c.max_rollout_reward
                best_sql = SQLSelector._tiebreak_pick(c.variants, db_connector=db_connector)
        return best_sql

    @staticmethod
    def _maybe_topk_bootstrap_pick(
        rollout_stats_list: List[Dict[str, Any]],
        clusters: Dict[str, _Cluster],
        votes,
        fallback_sql: str,
        *,
        question: str = "",
        schema_ddl: str = "",
        db_connector=None,
    ) -> str:
        """Oracle-free ambiguous tiebreak among top vote clusters (no gold, no LLM)."""
        if not SQLSelector._topk_bootstrap_enabled():
            return fallback_sql

        from .gated_selection import _analyze_r4_gate
        from .confidence_core import confidence_aware_selection, env_float, env_int

        rss = rollout_stats_list or []
        gate = _analyze_r4_gate(rss, env_float("MCTS_R4_GATE_MARGIN", 0.7))
        if not gate.ambiguous:
            return fallback_sql

        ranked = SQLSelector._ranked_r4_cluster_sigs(rss, clusters, votes)
        mode = SQLSelector._topk_bootstrap_mode()
        if mode == "ambig_purity" and len(ranked) >= 2:
            sig_a, sig_b = ranked[0], ranked[1]
            pa = SQLSelector._sig_v2_purity(rss, sig_a, clusters)
            pb = SQLSelector._sig_v2_purity(rss, sig_b, clusters)
            va, vb = votes.get(sig_a, 0), votes.get(sig_b, 0)
            pick_sig = sig_a
            if pb > pa and vb >= va * env_float("MCTS_R4_GATE_MARGIN", 0.7):
                pick_sig = sig_b
            elif pb > pa + 0.1 and pb >= pa:
                pick_sig = sig_b
            if pick_sig != sig_a:
                c = clusters.get(pick_sig)
                if c:
                    alt = SQLSelector._tiebreak_pick(c.variants, db_connector=db_connector)
                    if alt:
                        print(
                            f"[Selection] R4 ambig_purity: pick sig={pick_sig[:16]}… "
                            f"purity={pb:.2f}>{pa:.2f} gate={gate.gate_reason}"
                        )
                        return alt
            return fallback_sql

        if mode not in ("1", "true", "yes", "on", "bootstrap"):
            return fallback_sql

        if db_connector is None:
            return fallback_sql

        top_k = min(SQLSelector._topk_clusters(), len(ranked))
        if top_k < 2:
            return fallback_sql

        pool = SQLSelector._sqls_for_cluster_sigs(clusters, ranked[:top_k])
        if len(pool) <= 1:
            return fallback_sql

        def exec_fn(sql: str):
            df, err = db_connector.execute_query(sql)
            return df, err

        sel = confidence_aware_selection(
            pool,
            question=question,
            schema=schema_ddl,
            execute_fn=exec_fn,
            threshold=env_float("MCTS_CONFIDENCE_THRESHOLD", 0.7),
            top_k=min(env_int("MCTS_CONFIDENCE_TOP_K", 3), len(pool)),
            vote_samples=env_int("MCTS_CONFIDENCE_VOTE_SAMPLES", 3),
            llm_call=None,
            db_connector=db_connector,
        )
        picked = (sel.sql or fallback_sql).strip()
        if picked and picked != fallback_sql.strip():
            print(
                f"[Selection] R4 topk_bootstrap: mode={sel.mode} conf={sel.top_confidence:.3f} "
                f"top_k={top_k} gate={gate.gate_reason}"
            )
        return picked or fallback_sql

    @staticmethod
    def _struct_with_bias_pick(
        rollout_stats_list: List[Dict[str, Any]],
        clusters: Dict[str, _Cluster],
        votes,
        *,
        db_connector=None,
    ) -> str:
        ranked = votes.most_common()
        if not ranked:
            return ""
        top_v = ranked[0][1]
        margin = SQLSelector._with_bias_margin()
        close = [ranked[0][0]]
        if len(ranked) >= 2 and ranked[1][1] * margin >= top_v:
            close.append(ranked[1][0])
        with_sigs = []
        for sig in close:
            c = clusters.get(sig)
            if not c:
                continue
            if any(re.search(r"\bWITH\b", sql, re.I) for sql, _, _ in c.variants):
                with_sigs.append(sig)
        pick_sig = with_sigs[0] if with_sigs else ranked[0][0]
        print(f"[Selection] R4 struct_with_bias: pick sig={pick_sig[:16]}…")
        c = clusters.get(pick_sig)
        if not c:
            return ""
        return SQLSelector._tiebreak_pick(c.variants, db_connector=db_connector)

    @staticmethod
    def resolve_strategy(strategy: Optional[str] = None) -> str:
        raw = (strategy or os.environ.get(STRATEGY_ENV, "R0") or "R0").strip()
        return _STRATEGY_ALIASES.get(raw, _STRATEGY_ALIASES.get(raw.upper(), "R0"))

    @staticmethod
    def select(
        rollout_stats_list: List[Dict[str, Any]],
        strategy: Optional[str] = None,
        *,
        question: str = "",
        schema_ddl: str = "",
        db_connector=None,
        llm_config: Optional[dict] = None,
    ) -> str:
        """Final SQL pick; default R0 unless MCTS_SELECTOR_STRATEGY is set."""
        sid = SQLSelector.resolve_strategy(strategy)
        if sid == "R4":
            from .gated_selection import confidence_mode_enabled, gated_r4_r8_select

            if confidence_mode_enabled():
                sql, _ = gated_r4_r8_select(
                    rollout_stats_list,
                    question=question,
                    schema_ddl=schema_ddl,
                    db_connector=db_connector,
                    llm_config=llm_config,
                )
                return sql
            return SQLSelector._select_r4_majority_then_reward(
                rollout_stats_list,
                db_connector=db_connector,
                question=question,
                schema_ddl=schema_ddl,
            )
        if sid == "R2":
            return SQLSelector._select_r2_max_cluster_visit(
                rollout_stats_list, db_connector=db_connector
            )
        if sid == "R3":
            return SQLSelector._select_r3_reward_x_size(
                rollout_stats_list, db_connector=db_connector
            )
        return SQLSelector.select_by_highest_reward(rollout_stats_list)

    @staticmethod
    def _tiebreak_pick(
        variants: List[Tuple[str, float, int]],
        *,
        db_connector=None,
    ) -> str:
        from .execution_tiebreak import tiebreak_pick_variants

        return tiebreak_pick_variants(variants, db_connector=db_connector)

    @staticmethod
    def _build_clusters(rss: List[Dict[str, Any]]) -> Dict[str, _Cluster]:
        clusters: Dict[str, _Cluster] = {}
        for r in rss:
            rb = r.get("result_buckets") or {}
            rw = float(r.get("reward", 0.0))
            leaf_v = int(r.get("leaf_visit_count") or 0)
            if not leaf_v and r.get("visit_counts"):
                vc = r.get("visit_counts")
                leaf_v = int((vc[-1] if isinstance(vc, list) else 0) or 0)
            for sig, cnt in rb.items():
                if not sig:
                    continue
                c = clusters.setdefault(sig, _Cluster(sig=sig))
                c.total_count += int(cnt)
                c.total_visit += leaf_v
            for v in r.get("all_sql_variants") or []:
                sig = v.get("result_signature") or ""
                if not sig:
                    continue
                c = clusters.setdefault(sig, _Cluster(sig=sig))
                rows = int(v.get("result_row_count") or 0) if v.get("valid") else 0
                c.variants.append((v.get("sql", ""), rw, rows))
        return clusters

    @staticmethod
    def _select_r2_max_cluster_visit(
        rollout_stats_list: List[Dict[str, Any]],
        *,
        db_connector=None,
    ) -> str:
        clusters = SQLSelector._build_clusters(rollout_stats_list)
        if not clusters:
            return SQLSelector.select_by_highest_reward(rollout_stats_list)
        best_sig = max(clusters, key=lambda s: clusters[s].total_visit)
        print(
            f"[Selection] R2: max total_visit cluster sig={best_sig[:16]}… "
            f"visit={clusters[best_sig].total_visit}"
        )
        return SQLSelector._tiebreak_pick(clusters[best_sig].variants, db_connector=db_connector)

    @staticmethod
    def _select_r4_majority_then_reward(
        rollout_stats_list: List[Dict[str, Any]],
        *,
        db_connector=None,
        question: str = "",
        schema_ddl: str = "",
    ) -> str:
        rss = SQLSelector._maybe_final_jaccard_rollouts(
            rollout_stats_list, db_connector=db_connector
        )
        clusters = SQLSelector._build_clusters(rss)
        if not clusters:
            return SQLSelector.select_by_highest_reward(rollout_stats_list)

        from .r4_vote import collect_r4_cluster_votes

        votes = collect_r4_cluster_votes(rss)
        if not votes:
            return SQLSelector.select_by_highest_reward(rollout_stats_list)

        cluster_sql = SQLSelector._pick_r4_cluster_sql_from_rss(
            rss, db_connector=db_connector
        )
        return SQLSelector._maybe_topk_bootstrap_pick(
            rss,
            clusters,
            votes,
            cluster_sql,
            question=question,
            schema_ddl=schema_ddl,
            db_connector=db_connector,
        )

    @staticmethod
    def _select_r3_reward_x_size(
        rollout_stats_list: List[Dict[str, Any]],
        *,
        db_connector=None,
    ) -> str:
        clusters = SQLSelector._build_clusters(rollout_stats_list)
        if not clusters:
            return SQLSelector.select_by_highest_reward(rollout_stats_list)
        best_sig = max(
            clusters,
            key=lambda s: clusters[s].max_rollout_reward * max(1, clusters[s].total_count),
        )
        print(
            f"[Selection] R3: max reward×size cluster sig={best_sig[:16]}… "
            f"score={clusters[best_sig].max_rollout_reward * max(1, clusters[best_sig].total_count):.4f}"
        )
        return SQLSelector._tiebreak_pick(clusters[best_sig].variants, db_connector=db_connector)

    @staticmethod
    def select_by_highest_reward(rollout_stats_list: List[Dict[str, Any]]) -> str:
        """
        策略：选择最高奖励的rollout的SQL（与merge_and_evaluate_sqls.py的max_reward策略一致）
        
        选择逻辑：
        1. 选择reward最高的rollout（如果有多个，收集所有）
        2. 从每个rollout的result_buckets中找到count最高的signature
        3. 如果有多个平票，选择第一个
        4. 从all_sql_variants中找到对应的SQL
        5. 如果有多个rollout具有相同最高reward，合并它们的SQL，然后选择结果行数最少 → SQL最短的
        
        Args:
            rollout_stats_list: 所有rollout的统计信息列表，包含reward、result_buckets、all_sql_variants
            
        Returns:
            最佳 SQL 字符串，如果找不到则返回空字符串
        """
        if not rollout_stats_list:
            print("[Selection] ⚠️ 没有rollout_stats，无法选择SQL")
            return ""
        
        print("[Selection] 使用策略：选择最高奖励的rollout的SQL（max_reward策略）")
        
        # 过滤掉没有result_buckets的rollout
        valid_rollouts = [r for r in rollout_stats_list if r.get('result_buckets')]
        
        if not valid_rollouts:
            print("[Selection] ❌ 未找到有效的rollout（没有result_buckets），无法选择SQL")
            return ""
        
        # 第一步：找到最高reward
        max_reward = max((r.get('reward', 0.0) for r in valid_rollouts), default=-1.0)
        
        # 第二步：收集所有具有最高reward的rollout
        top_reward_rollouts = [
            r for r in valid_rollouts 
            if abs(r.get('reward', 0.0) - max_reward) < 1e-6
        ]
        
        if not top_reward_rollouts:
            print("[Selection] ❌ 未找到有效的rollout")
            return ""
        
        print(f"[Selection] 找到 {len(top_reward_rollouts)} 个rollout具有最高reward {max_reward:.4f}")
        
        # 第三步：从每个rollout中提取SQL（找到result_buckets中count最高的signature对应的SQL）
        candidate_sqls = []  # 存储 (sql, result_buckets, signature, row_count) 元组
        
        for rollout in top_reward_rollouts:
            result_buckets = rollout.get('result_buckets', {})
            if not result_buckets:
                continue
            
            # 找到count最高的signature
            max_count = max(result_buckets.values())
            best_signatures = [sig for sig, count in result_buckets.items() if count == max_count]
            
            # 如果有多个平票，选择第一个
            best_signature = best_signatures[0] if best_signatures else None
            
            if not best_signature:
                continue
            
            # 从all_sql_variants中找到这个signature对应的SQL
            all_sql_variants = rollout.get('all_sql_variants', [])
            found_sql = None
            found_row_count = 0
            
            for sql_info in all_sql_variants:
                sql_signature = sql_info.get('result_signature')
                if sql_signature == best_signature:
                    found_sql = sql_info.get('sql', '')
                    if sql_info.get('valid', False):
                        found_row_count = sql_info.get('result_row_count', 0)
                    break
            
            if found_sql:
                candidate_sqls.append((found_sql, result_buckets, best_signature, found_row_count))
                print(f"[Selection] 从rollout {rollout.get('rollout_id', '?')} 提取SQL: signature={best_signature}, count={max_count}, row_count={found_row_count}")
        
        if not candidate_sqls:
            print("[Selection] ❌ 未找到有效的SQL")
            return ""
        
        # 第四步：如果有多个候选SQL，使用tiebreak逻辑选择最佳SQL
        if len(candidate_sqls) == 1:
            best_sql = candidate_sqls[0][0]
            print(f"[Selection] ✅ 选择唯一候选SQL")
        else:
            # 多个候选SQL，使用tiebreak：结果行数最少 → 列数最少 → SQL最短
            print(f"[Selection] 有 {len(candidate_sqls)} 个候选SQL，使用tiebreak逻辑")
            
            def get_tiebreak_score(item: tuple) -> tuple:
                """返回(行数, SQL长度)，越小越好"""
                sql, _, _, row_count = item
                num_rows = row_count if row_count else 0
                sql_len = len(sql) if sql else 0
                return (num_rows, sql_len)
            
            best_item = min(candidate_sqls, key=get_tiebreak_score)
            best_sql = best_item[0]
            best_score = get_tiebreak_score(best_item)
            print(f"[Selection] ✅ 选择最佳SQL (行数={best_score[0]}, SQL长度={best_score[1]})")
        
        return best_sql.strip() if best_sql else ""
    
    @staticmethod
    def _has_unnecessary_aggregation(sql: str) -> bool:
        """
        检查SQL是否包含可能不必要的聚合函数（MAX/MIN），
        用于在相同reward和sql_bucket_count时，优先选择不使用聚合的SQL
        
        Args:
            sql: SQL字符串
            
        Returns:
            如果包含可能不必要的聚合函数返回True
        """
        if not sql:
            return False
        
        sql_upper = sql.upper()
        # 检查是否包含MAX或MIN聚合函数
        # 简单检查：如果包含MAX(...)或MIN(...)模式
        import re
        # 匹配 MAX( 或 MIN( 模式
        has_max_min = bool(re.search(r'\b(MAX|MIN)\s*\(', sql_upper))
        
        return has_max_min
    
    @staticmethod
    def _calculate_avg_reward(rollout_stats: Dict[str, Any]) -> float:
        """
        计算rollout中所有SQL变体的平均奖励（基于所有bucket的加权平均）
        
        对于每个SQL变体，如果它的结果属于某个bucket，给予该bucket的权重分数
        （bucket计数/总变体数）。然后对所有SQL变体的分数求平均。
        
        这样，如果一个rollout的所有SQL变体都返回相同结果（都在最佳bucket），
        平均奖励就是1.0。如果SQL变体分散在不同bucket，平均奖励会较低。
        
        Args:
            rollout_stats: rollout统计信息，包含all_sql_variants和result_buckets
            
        Returns:
            平均奖励值（0.0到1.0之间）
        """
        all_sql_variants = rollout_stats.get('all_sql_variants', [])
        result_buckets = rollout_stats.get('result_buckets', {})
        
        if not all_sql_variants or not result_buckets:
            # 如果没有SQL变体或结果分桶，返回rollout的总体reward
            return rollout_stats.get('reward', 0.0)
        
        total_variants = len(all_sql_variants)
        if total_variants == 0:
            return 0.0
        
        # 计算每个bucket的权重分数 = bucket计数 / 总变体数
        bucket_weights = {}
        for bucket_signature, bucket_count in result_buckets.items():
            bucket_weights[bucket_signature] = bucket_count / float(total_variants)
        
        # 对于每个SQL变体，计算它的奖励分数
        total_score = 0.0
        valid_count = 0
        
        for sql_info in all_sql_variants:
            if sql_info.get('valid', False):
                sql_signature = sql_info.get('result_signature')
                if sql_signature and sql_signature in bucket_weights:
                    # 该SQL变体的奖励 = 它所属bucket的权重分数
                    total_score += bucket_weights[sql_signature]
                else:
                    # 如果SQL变体有效但没有结果签名，给予0分
                    total_score += 0.0
                valid_count += 1
            else:
                # 无效的SQL变体给予0分
                total_score += 0.0
                valid_count += 1
        
        # 平均奖励 = 所有SQL变体的分数总和 / 总变体数
        if valid_count == 0:
            return 0.0
        
        avg_reward = total_score / float(total_variants)
        
        return avg_reward

