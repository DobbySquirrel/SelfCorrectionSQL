import autogen
from utils.prompts import Prompts
from utils.code_executor import CodeExecutor
from core.database_connector import DatabaseConnector
from utils.agent_helpers import AgentHelpers
from workflows.straightforward_workflow import StraightforwardWorkflow
from workflows.schema_navigator import SchemaNavigator
from workflows.a_star_workflow import AStarWorkflow
from workflows.generate_function_workflow import GenerateFunctionWorkflow

class AgentSystem:
    """使用AutoGen实现的Agent系统"""
    
    def __init__(self, llm_config, db_connector):
        self.llm_config = llm_config
        self.db_connector = db_connector
        self.code_executor = CodeExecutor(db_connector)
        self.setup_agents()
        self.helpers = AgentHelpers()
        
    def setup_agents(self):
        """设置各个Agent"""
        # 生成器Agent - 负责生成子节点和Actions
        self.generator = autogen.AssistantAgent(
            name="Generator",
            llm_config=self.llm_config,
            system_message=Prompts.GENERATOR_SYSTEM
        )
        
        # 修复器Agent - 负责修复代码错误
        self.fixer = autogen.AssistantAgent(
            name="Fixer",
            llm_config=self.llm_config,
            system_message=Prompts.FIXER_SYSTEM
        )
        
        # 判断器Agent - 负责评估结果
        self.judger = autogen.AssistantAgent(
            name="Judger",
            llm_config=self.llm_config,
            system_message=Prompts.JUDGER_SYSTEM
        )
        
        # 答案格式Agent - 负责描述答案格式
        self.answer_format = autogen.AssistantAgent(
            name="AnswerFormat",
            llm_config=self.llm_config,
            system_message=Prompts.question_format_system
        )
        
        # 对齐器Agent - 负责对齐SQL和代码执行结果
        self.aligner = autogen.AssistantAgent(
            name="Aligner",
            llm_config=self.llm_config,
            system_message=Prompts.ALIGNER_SYSTEM
        )
        
        # 用户代理 - 协调其他Agent
        self.user_proxy = autogen.UserProxyAgent(
            name="UserProxy",
            human_input_mode="NEVER",
            max_consecutive_auto_reply=0,
            code_execution_config=False  # 禁用自动代码执行
        )
        # SQL执行器 - 用于在辩论过程中执行SQL
        self.schema_agent = autogen.AssistantAgent(
            name="SchemaAgent",
            llm_config=self.llm_config,
            system_message=Prompts.SCHEMA_AGENT_SYSTEM
        )
        
        # Initialize additional agents for GenerateFunctionWorkflow
        self.python_code_generator = autogen.AssistantAgent(
            name="PythonCodeGenerator",
            llm_config=self.llm_config,
            system_message="我是一个Python代码生成器，负责根据需求生成Python函数。"
        )
        self.semantics_evaluator = autogen.AssistantAgent(
            name="SemanticsEvaluator",
            llm_config=self.llm_config,
            system_message="我是一个语义评估器，负责检查生成的代码是否符合语义要求。"
        )
        self.problem_diagnoser = autogen.AssistantAgent(
            name="ProblemDiagnoser",
            llm_config=self.llm_config,
            system_message="我是一个问题诊断器，负责识别和诊断代码中的问题。"
        )

        # 初始化工作流处理器
        self.straightforward_workflow = StraightforwardWorkflow(
            self.user_proxy, self.generator, self.judger, self.aligner, self.code_executor,
            self.answer_format,self.semantics_evaluator
        )
        self.schema_navigator = SchemaNavigator(
            user_proxy=self.user_proxy,
            schema_agent=self.schema_agent
        )
        self.generate_function_workflow = GenerateFunctionWorkflow(
            user_proxy=self.user_proxy,
            python_code_generator=self.python_code_generator,
            semantics_evaluator=self.semantics_evaluator,
            problem_diagnoser=self.problem_diagnoser,
            code_executor=self.code_executor
        )
    
    def _solve_straightforward(self, node, tree, db_name, schema_info=None, additional_context="", 
                               tables_schema_first_three=None, example_data=None, sql_pandas=None, sql_salchemy=None,related_python_code=None,analysis_based_on_few_shot_logic=None,df_list=None,id=None):
        """直接工作流解决问题 - 委托给StraightforwardWorkflow类处理"""
        return self.straightforward_workflow.solve(
            node, tree, db_name, schema_info, additional_context,
            tables_schema_first_three, example_data, sql_pandas, sql_salchemy,related_python_code,analysis_based_on_few_shot_logic,df_list,id
        )


    def _solve_generate_function(self, node, tree, db_name, Gold_sql=None,additional_context="",related_python_code=None,related_sql=None,operations=None):
        """生成函数工作流解决问题 - 委托给GenerateFunctionWorkflow类处理"""
        return self.generate_function_workflow.solve(
            node=node,
            tree=tree,
            db_name=db_name,
            Gold_sql=Gold_sql,
            additional_context=additional_context,
            related_python_code=related_python_code,
            related_sql=related_sql,
            operations=operations
        )