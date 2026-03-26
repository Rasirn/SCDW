from .tia_project_builder import ProjectBuildConfig, BlockSpec, build_tia_project

cfg = ProjectBuildConfig(
    public_api_dir=r"E:\PlcProject\SoftWares\Siemens\Automation\Portal V17\PublicAPI\V17",
    project_root=r"E:\PlcProject\Code\PLC\tia_python_demo_output",
    project_name="MyAutoProject01",
    cpu_order_number="OrderNumber:6ES7 510-1DJ01-0AB0/V2.0",
    overwrite_existing_project_dir=True,
)

LAD_XML_TEST = """<?xml version="1.0" encoding="utf-8"?>
<Document>
  <Engineering version="V17" />
  <DocumentInfo>
    <Created>2026-03-13T12:00:00.0000000Z</Created>
    <ExportSetting>WithDefaults</ExportSetting>
    <InstalledProducts>
      <Product>
        <DisplayName>Totally Integrated Automation Portal</DisplayName>
        <DisplayVersion>V17 Update 4</DisplayVersion>
      </Product>
      <OptionPackage>
        <DisplayName>TIA Portal Openness</DisplayName>
        <DisplayVersion>V17 Update 4</DisplayVersion>
      </OptionPackage>
      <Product>
        <DisplayName>STEP 7 Professional</DisplayName>
        <DisplayVersion>V17 Update 4</DisplayVersion>
      </Product>
    </InstalledProducts>
  </DocumentInfo>

  <SW.Blocks.FC ID="0">
    <AttributeList>
      <AutoNumber>true</AutoNumber>
      <HeaderAuthor />
      <HeaderFamily />
      <HeaderName />
      <HeaderVersion>0.1</HeaderVersion>
      <Interface>
        <Sections xmlns="http://www.siemens.com/automation/Openness/SW/Interface/v5">
          <Section Name="Input" />
          <Section Name="Output" />
          <Section Name="InOut" />
          <Section Name="Temp" />
          <Section Name="Constant" />
          <Section Name="Return">
            <Member Name="Ret_Val" Datatype="Void" Accessibility="Public" />
          </Section>
        </Sections>
      </Interface>
      <IsIECCheckEnabled>false</IsIECCheckEnabled>
      <MemoryLayout>Optimized</MemoryLayout>
      <Name>Demo_LAD_FC</Name>
      <Number>101</Number>
      <ProgrammingLanguage>LAD</ProgrammingLanguage>
      <SetENOAutomatically>false</SetENOAutomatically>
      <UDABlockProperties />
      <UDAEnableTagReadback>false</UDAEnableTagReadback>
    </AttributeList>

    <ObjectList>
      <MultilingualText ID="1" CompositionName="Comment">
        <ObjectList>
          <MultilingualTextItem ID="2" CompositionName="Items">
            <AttributeList>
              <Culture>zh-CN</Culture>
              <Text />
            </AttributeList>
          </MultilingualTextItem>
        </ObjectList>
      </MultilingualText>

      <SW.Blocks.CompileUnit ID="3" CompositionName="CompileUnits">
        <AttributeList>
          <NetworkSource>
            <FlgNet xmlns="http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4">
              <Parts>
                <Access Scope="GlobalVariable" UId="21">
                  <Symbol>
                    <Component Name="测试启动" />
                  </Symbol>
                </Access>

                <Access Scope="GlobalVariable" UId="22">
                  <Symbol>
                    <Component Name="测试输出" />
                  </Symbol>
                </Access>

                <Part Name="Contact" UId="23" />
                <Part Name="Coil" UId="24" />
              </Parts>

              <Wires>
                <Wire UId="25">
                  <Powerrail />
                  <NameCon UId="23" Name="in" />
                </Wire>

                <Wire UId="26">
                  <IdentCon UId="21" />
                  <NameCon UId="23" Name="operand" />
                </Wire>

                <Wire UId="27">
                  <NameCon UId="23" Name="out" />
                  <NameCon UId="24" Name="in" />
                </Wire>

                <Wire UId="28">
                  <IdentCon UId="22" />
                  <NameCon UId="24" Name="operand" />
                </Wire>
              </Wires>
            </FlgNet>
          </NetworkSource>
          <ProgrammingLanguage>LAD</ProgrammingLanguage>
        </AttributeList>

        <ObjectList>
          <MultilingualText ID="4" CompositionName="Comment">
            <ObjectList>
              <MultilingualTextItem ID="5" CompositionName="Items">
                <AttributeList>
                  <Culture>zh-CN</Culture>
                  <Text />
                </AttributeList>
              </MultilingualTextItem>
            </ObjectList>
          </MultilingualText>

          <MultilingualText ID="6" CompositionName="Title">
            <ObjectList>
              <MultilingualTextItem ID="7" CompositionName="Items">
                <AttributeList>
                  <Culture>zh-CN</Culture>
                  <Text>Demo 网络1</Text>
                </AttributeList>
              </MultilingualTextItem>
            </ObjectList>
          </MultilingualText>
        </ObjectList>
      </SW.Blocks.CompileUnit>

      <MultilingualText ID="8" CompositionName="Title">
        <ObjectList>
          <MultilingualTextItem ID="9" CompositionName="Items">
            <AttributeList>
              <Culture>zh-CN</Culture>
              <Text>Demo_LAD_FC</Text>
            </AttributeList>
          </MultilingualTextItem>
        </ObjectList>
      </MultilingualText>
    </ObjectList>
  </SW.Blocks.FC>
</Document>"""

blocks = [
    BlockSpec(
        language="SCL",
        name="OB1",
        content='''
ORGANIZATION_BLOCK "Main"
{ S7_Optimized_Access := 'TRUE' }
VERSION : 0.1
BEGIN
    IF %I0.0 AND NOT %I0.1 THEN
        %Q0.0 := TRUE;
    END_IF;

    IF %I0.1 THEN
        %Q0.0 := FALSE;
    END_IF;
END_ORGANIZATION_BLOCK
'''.strip()
    ),

    # LAD 这里要传 XML，不是纯文本 LAD
    BlockSpec(
        language="LAD",
        name="FB100",
        content=LAD_XML_TEST
    )
]

cfg = ProjectBuildConfig(
    public_api_dir=r"E:\PlcProject\SoftWares\Siemens\Automation\Portal V17\PublicAPI\V17",
    project_root=r"E:\PlcProject\Code\PLC\tia_python_demo_output",
    project_name="MyAutoProject01",
    cpu_order_number="OrderNumber:6ES7 510-1DJ01-0AB0/V2.0",
    overwrite_existing_project_dir=True,
)

# result = build_tia_project(cfg, blocks)

# print(result.project_path)
# print(result.compile_state)
# print(result.compile_messages)

def create_plc_demo(project_name):
    cfg = ProjectBuildConfig(
        public_api_dir=r"E:\PlcProject\SoftWares\Siemens\Automation\Portal V17\PublicAPI\V17",
        project_root=r"E:\PlcProject\Code\PLC\tia_python_demo_output",
        project_name=project_name,
        cpu_order_number="OrderNumber:6ES7 510-1DJ01-0AB0/V2.0",
        overwrite_existing_project_dir=True,
    )
    result = build_tia_project(cfg, blocks)

    results = f"【创建测试项目工具被调用】\n"
    results += f"- 项目名称：{project_name}\n"
    results += f"- 保存路径：{result.project_path}\n"
    results += f"- TIA版本：V17\n"
    results += f"✅ 已成功创建TIA博途项目：{project_name}"
    return results

# create_plc_demo("MyAutoProject02")