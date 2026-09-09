import config from './playwright.config.js'; export default {...config,testMatch:'checkbox.spec.js',timeout:60000,outputDir:'/artifacts/web-graph-checkbox',reporter:[['list']]};
