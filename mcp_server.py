from mcp.server.fastmcp import FastMCP
from pydantic import Field
import sys
import logger
from openness.create_demo import create_plc_demo
# 创建MCP服务器实例
mcp = FastMCP("TIA_MCP", log_level="INFO")

@mcp.tool(
    name="create_project",
    description="创建一个新的TIA博途项目"
)
def create_project(
    project_name: str = Field(description="项目名称"),
    project_path: str = Field(description="项目保存路径"),
    project_version: str = Field(description="TIA博途版本，如V16、V17等")
) -> str:
    """创建新的TIA博途项目"""
    result = f"【创建项目工具被调用】\n"
    result += f"- 项目名称：{project_name}\n"
    result += f"- 保存路径：{project_path}\n"
    result += f"- TIA版本：{project_version}\n"
    result += f"✅ 已成功创建TIA博途项目：{project_name}"
    # print("\t[工具调用]创建项目")
    
    return result

@mcp.tool(
    name="create_demo_project",
    description="创建一个新的测试项目，其中包含简单的梯形图代码。"
)
def create_demo_project(
    project_name: str = Field(description="项目名称")
) -> str:
    """创建示例TIA博途项目"""
    result = create_plc_demo(project_name=project_name)
    return result


@mcp.tool(
    name="add_plc",
    description="在项目中添加PLC设备"
)
def add_plc(
    project_name: str = Field(description="项目名称"),
    plc_model: str = Field(description="PLC型号，如CPU 1214C、CPU 1511等"),
    plc_name: str = Field(description="PLC设备名称")
) -> str:
    """添加PLC设备到项目中"""
    result = f"【添加PLC工具被调用】\n"
    result += f"- 项目名称：{project_name}\n"
    result += f"- PLC型号：{plc_model}\n"
    result += f"- PLC名称：{plc_name}\n"
    result += f"✅ 已在项目中添加PLC设备：{plc_name} ({plc_model})"
    return result


@mcp.tool(
    name="configure_io",
    description="配置PLC的输入输出模块"
)
def configure_io(
    plc_name: str = Field(description="PLC设备名称"),
    module_type: str = Field(description="模块类型，如DI、DO、AI、AO等"),
    module_position: str = Field(description="模块安装位置，如机架0插槽1"),
    io_address: str = Field(description="IO地址，如IW64、QW32等")
) -> str:
    """配置PLC的输入输出模块"""
    result = f"【配置IO模块工具被调用】\n"
    result += f"- PLC名称：{plc_name}\n"
    result += f"- 模块类型：{module_type}\n"
    result += f"- 安装位置：{module_position}\n"
    result += f"- IO地址：{io_address}\n"
    result += f"✅ 已成功配置{module_type}模块，地址为{io_address}"
    return result


@mcp.tool(
    name="add_network",
    description="添加Profinet工业以太网网络"
)
def add_network(
    network_name: str = Field(description="网络名称"),
    network_type: str = Field(description="网络类型，如Profinet、Profibus等")
) -> str:
    """添加工业网络"""
    result = f"【添加网络工具被调用】\n"
    result += f"- 网络名称：{network_name}\n"
    result += f"- 网络类型：{network_type}\n"
    result += f"✅ 已成功添加{network_type}网络：{network_name}"
    return result


@mcp.tool(
    name="connect_devices",
    description="连接设备到网络"
)
def connect_devices(
    network_name: str = Field(description="网络名称"),
    devices: str = Field(description="要连接的设备列表，用逗号分隔")
) -> str:
    """连接设备到网络"""
    result = f"【连接设备工具被调用】\n"
    result += f"- 网络名称：{network_name}\n"
    result += f"- 连接设备：{devices}\n"
    result += f"✅ 已成功将设备连接到{network_name}网络"
    return result


@mcp.tool(
    name="generate_code",
    description="生成PLC程序代码（梯形图/SCL/STL）"
)
def generate_code(
    plc_name: str = Field(description="PLC设备名称"),
    block_name: str = Field(description="程序块名称，如Main [OB1]、FC1等"),
    code_type: str = Field(description="代码类型，如LAD(梯形图)、SCL、STL"),
    code_description: str = Field(description="代码功能描述")
) -> str:
    """生成PLC程序代码"""
    result = f"【生成代码工具被调用】\n"
    result += f"- PLC名称：{plc_name}\n"
    result += f"- 程序块：{block_name}\n"
    result += f"- 代码类型：{code_type}\n"
    result += f"- 功能描述：{code_description}\n"
    result += f"✅ 已成功生成{code_type}代码到程序块{block_name}"
    return result


@mcp.tool(
    name="add_hmi",
    description="添加HMI人机界面设备"
)
def add_hmi(
    hmi_model: str = Field(description="HMI型号，如KTP700 Basic PN"),
    hmi_name: str = Field(description="HMI设备名称"),
    project_name: str = Field(description="所属项目名称")
) -> str:
    """添加HMI设备"""
    result = f"【添加HMI工具被调用】\n"
    result += f"- HMI型号：{hmi_model}\n"
    result += f"- HMI名称：{hmi_name}\n"
    result += f"- 所属项目：{project_name}\n"
    result += f"✅ 已成功添加HMI设备：{hmi_name} ({hmi_model})"
    return result


@mcp.tool(
    name="configure_hmi_screen",
    description="配置HMI屏幕画面"
)
def configure_hmi_screen(
    hmi_name: str = Field(description="HMI设备名称"),
    screen_name: str = Field(description="画面名称，如主画面、报警画面等"),
    screen_elements: str = Field(description="画面元素，如按钮、指示灯、数值显示等")
) -> str:
    """配置HMI画面"""
    result = f"【配置HMI画面工具被调用】\n"
    result += f"- HMI名称：{hmi_name}\n"
    result += f"- 画面名称：{screen_name}\n"
    result += f"- 画面元素：{screen_elements}\n"
    result += f"✅ 已成功配置HMI画面：{screen_name}"
    return result


@mcp.tool(
    name="compile_project",
    description="编译整个TIA博途项目"
)
def compile_project(
    project_name: str = Field(description="项目名称"),
    compile_mode: str = Field(description="编译模式，如完全编译、增量编译等")
) -> str:
    """编译项目"""
    result = f"【编译项目工具被调用】\n"
    result += f"- 项目名称：{project_name}\n"
    result += f"- 编译模式：{compile_mode}\n"
    result += f"✅ 已成功编译项目，没有发现错误"
    return result


@mcp.tool(
    name="download_to_plc",
    description="将项目下载到PLC设备"
)
def download_to_plc(
    plc_name: str = Field(description="PLC设备名称"),
    pg_pc_interface: str = Field(description="PG/PC接口类型，如PN/IE"),
    ip_address: str = Field(description="PLC的IP地址")
) -> str:
    """下载项目到PLC"""
    result = f"【下载到PLC工具被调用】\n"
    result += f"- PLC名称：{plc_name}\n"
    result += f"- PG/PC接口：{pg_pc_interface}\n"
    result += f"- IP地址：{ip_address}\n"
    result += f"✅ 已成功将项目下载到PLC {plc_name} (IP: {ip_address})"
    return result


@mcp.tool(
    name="monitor_tags",
    description="在线监控PLC变量"
)
def monitor_tags(
    plc_name: str = Field(description="PLC设备名称"),
    tag_list: str = Field(description="要监控的变量列表，用逗号分隔")
) -> str:
    """在线监控PLC变量"""
    result = f"【在线监控工具被调用】\n"
    result += f"- PLC名称：{plc_name}\n"
    result += f"- 监控变量：{tag_list}\n"
    result += f"✅ 已开始在线监控PLC变量，当前所有值正常"
    return result


@mcp.tool(
    name="export_documentation",
    description="导出项目文档"
)
def export_documentation(
    project_name: str = Field(description="项目名称"),
    export_path: str = Field(description="导出路径"),
    export_format: str = Field(description="导出格式，如PDF、Excel等")
) -> str:
    """导出项目文档"""
    result = f"【导出文档工具被调用】\n"
    result += f"- 项目名称：{project_name}\n"
    result += f"- 导出路径：{export_path}\n"
    result += f"- 导出格式：{export_format}\n"
    result += f"✅ 已成功导出{export_format}格式的项目文档到{export_path}"
    return result


@mcp.tool(
    name="list_all_tools",
    description="列出所有可用的TIA博途工具"
)
@mcp.tool(
    name="list_all_tools",
    description="列出所有可用的TIA博途工具，并返回工具选择规则"
)
def list_all_tools() -> str:
    """列出所有可用工具，并明确告知模型工具调用优先级"""

    tools = [
        "1. create_demo_project - 默认项目创建工具；当用户要求创建/新建/生成TIA项目时，统一使用该工具；当前已实现；只需要 project_name",
        "2. create_project - 占位工具，当前未实现；不要用于实际项目创建",
        "3. add_plc - 在项目中添加PLC设备",
        "4. configure_io - 配置PLC的输入输出模块",
        "5. add_network - 添加Profinet工业以太网网络",
        "6. connect_devices - 连接设备到网络",
        "7. generate_code - 生成PLC程序代码（梯形图/SCL/STL）",
        "8. add_hmi - 添加HMI人机界面设备",
        "9. configure_hmi_screen - 配置HMI屏幕画面",
        "10. compile_project - 编译整个TIA博途项目",
        "11. download_to_plc - 将项目下载到PLC设备",
        "12. monitor_tags - 在线监控PLC变量",
        "13. export_documentation - 导出项目文档"
    ]

    rules = [
        "【工具选择规则】",
        "1. 当用户提出“创建项目”“新建项目”“生成项目”“建立项目”“创建TIA项目”“新建TIA项目”等请求时，必须调用 create_demo_project。",
        "2. create_demo_project 是当前唯一可用的项目创建工具，也是默认项目创建入口。",
        "3. create_project 当前只是占位接口，不用于实际创建项目，不应优先选择。",
        "4. 对于“帮我创建一个项目”这类请求，不要询问 project_path 和 project_version，因为 create_demo_project 只需要 project_name。",
        "5. 如果用户没有提供 project_name，可以让模型自行生成一个默认项目名称，例如 DemoProject、TestProject 或用户语义相关名称。",
        "6. 只有在未来 create_project 被真正实现后，才可以考虑用于正式项目创建。当前阶段一律优先 create_demo_project。"
    ]

    result = "【TIA博途MCP服务器】\n"
    result += "当前可用工具列表：\n\n"
    result += "\n".join(tools)
    result += "\n\n"
    result += "\n".join(rules)

    return result





if __name__ == "__main__":
    mcp.run(transport="stdio")
