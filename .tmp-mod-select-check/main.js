import { createApp, h, ref } from 'vue';
import AppDialog from '/src/components/AppDialog.vue';
import OptionSelect from '/src/components/OptionSelect.vue';
import '/src/style.css';
const params = new URLSearchParams(location.search);
document.documentElement.dataset.theme = params.get('theme') || 'light';
const options = [
  {label:'旧值（不可选）',value:'__unmapped__',disabled:true},
  {label:'使用 MOD 默认值',value:''},
  {label:'关闭 false',value:'0'},
  {label:'数量 0',value:'1'},
  {label:'开启 true',value:'2'},
  {label:'文本空字符串',value:'3'},
];
createApp({setup(){
  const open=ref(false), value=ref('0'), second=ref('1'), saved=ref('尚未保存');
  const field=(label,model)=>h('div',{class:'mod-config-field'},[
    h('span',label),h(OptionSelect,{label,options,modelValue:model.value,'onUpdate:modelValue':v=>model.value=v})]);
  return()=>h('div',{class:'page'},[
    h('button',{class:'button',onClick:()=>open.value=true},'编辑配置'),
    h('p',{role:'status'},saved.value),
    h(AppDialog,{open:open.value,'onUpdate:open':v=>open.value=v,title:'编辑配置 · 回归 MOD',size:'lg'}, {
      default:()=>h('div',{class:'mod-config-editor'},[
        field('测试配置',value),h('p',`当前选择：${value.value===''?'默认':value.value}`),
        ...Array.from({length:12},(_,i)=>h('p',{style:'padding:12px 0'},`其他配置 ${i+1}`)),
        field('底部配置',second),h('p',`底部选择：${second.value===''?'默认':second.value}`),
      ]),
      footer:()=>[
        h('button',{class:'button',onClick:()=>open.value=false},'取消'),
        h('button',{class:'button primary',onClick:()=>{saved.value=`已保存：${value.value===''?'默认':value.value} / ${second.value===''?'默认':second.value}`;open.value=false;}},'保存配置'),
      ]
    }),
  ]);
}}).mount('#app');
