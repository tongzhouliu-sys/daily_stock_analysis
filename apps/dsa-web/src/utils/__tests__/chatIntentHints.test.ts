import { describe, it, expect } from 'vitest';
import { extractIntentHints } from '../chatIntentHints';

describe('extractIntentHints - Phase 2 Intent Inference', () => {
  describe('goal inference', () => {
    it('should infer worth_watching goal', () => {
      expect(extractIntentHints('茅台值得关注吗').goal).toBe('worth_watching');
      expect(extractIntentHints('这只股票值得买吗').goal).toBe('worth_watching');
      expect(extractIntentHints('能不能买腾讯').goal).toBe('worth_watching');
      expect(extractIntentHints('有没有投资价值').goal).toBe('worth_watching');
    });

    it('should infer evaluate_holding goal', () => {
      expect(extractIntentHints('我持仓的比亚迪').goal).toBe('evaluate_holding');
      expect(extractIntentHints('持有的茅台怎么操作').goal).toBe('evaluate_holding');
      expect(extractIntentHints('我的仓位').goal).toBe('evaluate_holding');
      expect(extractIntentHints('已经买了腾讯').goal).toBe('evaluate_holding');
      expect(extractIntentHints('手里有茅台').goal).toBe('evaluate_holding');
      expect(extractIntentHints('成本是3000').goal).toBe('evaluate_holding');
    });

    it('should infer understand_move goal', () => {
      expect(extractIntentHints('为什么涨停').goal).toBe('understand_move');
      expect(extractIntentHints('为何大跌').goal).toBe('understand_move');
      expect(extractIntentHints('涨跌原因是什么').goal).toBe('understand_move');
      expect(extractIntentHints('怎么突然涨了').goal).toBe('understand_move');
      expect(extractIntentHints('啥原因跌这么多').goal).toBe('understand_move');
    });

    it('should infer learn_method goal', () => {
      expect(extractIntentHints('怎么分析股票').goal).toBe('learn_method');
      expect(extractIntentHints('有什么分析方法').goal).toBe('learn_method');
      expect(extractIntentHints('如何分析基本面').goal).toBe('learn_method');
      expect(extractIntentHints('用什么方法分析').goal).toBe('learn_method');
    });

    it('should not infer goal when no pattern matches', () => {
      expect(extractIntentHints('分析一下茅台').goal).toBeUndefined();
      expect(extractIntentHints('600519').goal).toBeUndefined();
    });
  });

  describe('horizon inference', () => {
    it('should infer days horizon', () => {
      expect(extractIntentHints('短线看茅台').horizon).toBe('days');
      expect(extractIntentHints('几天内能涨吗').horizon).toBe('days');
      expect(extractIntentHints('今天能买吗').horizon).toBe('days');
      expect(extractIntentHints('这周走势如何').horizon).toBe('days');
    });

    it('should infer weeks horizon', () => {
      expect(extractIntentHints('短期走势如何').horizon).toBe('weeks');
      expect(extractIntentHints('一两周内能涨吗').horizon).toBe('weeks');
      expect(extractIntentHints('两三周后怎么样').horizon).toBe('weeks');
    });

    it('should infer months horizon', () => {
      expect(extractIntentHints('中期投资价值').horizon).toBe('months');
      expect(extractIntentHints('半年内能涨多少').horizon).toBe('months');
      expect(extractIntentHints('两三个月走势').horizon).toBe('months');
    });

    it('should infer long horizon', () => {
      expect(extractIntentHints('长期持有茅台').horizon).toBe('long');
      expect(extractIntentHints('一年以上走势').horizon).toBe('long');
      expect(extractIntentHints('长线投资腾讯').horizon).toBe('long');
    });

    it('should not infer horizon when no pattern matches', () => {
      expect(extractIntentHints('分析一下茅台').horizon).toBeUndefined();
    });
  });

  describe('position inference', () => {
    it('should infer not_held position', () => {
      expect(extractIntentHints('还没买茅台').position).toBe('not_held');
      expect(extractIntentHints('想入手腾讯').position).toBe('not_held');
      expect(extractIntentHints('准备买比亚迪').position).toBe('not_held');
      expect(extractIntentHints('空仓中').position).toBe('not_held');
    });

    it('should infer held_protect_profit position', () => {
      expect(extractIntentHints('持有盈利中').position).toBe('held_protect_profit');
      expect(extractIntentHints('持仓赚了不少').position).toBe('held_protect_profit');
      expect(extractIntentHints('已经赚了20%').position).toBe('held_protect_profit');
      expect(extractIntentHints('浮盈保护').position).toBe('held_protect_profit');
    });

    it('should infer held_control_drawdown position', () => {
      expect(extractIntentHints('持有亏损中').position).toBe('held_control_drawdown');
      expect(extractIntentHints('持仓被套了').position).toBe('held_control_drawdown');
      expect(extractIntentHints('已经亏了不少').position).toBe('held_control_drawdown');
      expect(extractIntentHints('套牢了怎么办').position).toBe('held_control_drawdown');
    });

    it('should not infer position when no pattern matches', () => {
      expect(extractIntentHints('分析一下茅台').position).toBeUndefined();
    });
  });

  describe('combined inference', () => {
    it('should infer multiple hints from complex message', () => {
      const hints = extractIntentHints('短线看茅台，我持仓的');
      expect(hints.goal).toBe('evaluate_holding');
      expect(hints.horizon).toBe('days');
    });

    it('should handle message with stock code and intent', () => {
      const hints = extractIntentHints('600519值得长期持有吗');
      expect(hints.extracted_stock_code).toBe('600519');
      expect(hints.inferred_market).toBe('a_share');
      expect(hints.goal).toBe('worth_watching');
      expect(hints.horizon).toBe('long');
      expect(hints.position).toBeUndefined(); // "长期持有" 是策略描述，不是持仓状态
    });

    it('should handle message with HK stock and position', () => {
      const hints = extractIntentHints('HK00700被套了怎么办');
      expect(hints.extracted_stock_code).toBe('HK00700');
      expect(hints.inferred_market).toBe('hk');
      expect(hints.position).toBe('held_control_drawdown');
    });

    it('should handle message with US stock and goal', () => {
      const hints = extractIntentHints('AAPL能不能买');
      expect(hints.extracted_stock_code).toBe('AAPL');
      expect(hints.inferred_market).toBe('us');
      expect(hints.goal).toBe('worth_watching');
    });
  });
});
