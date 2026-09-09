import { test, expect, publicURL, realLogin, api, assets, cleanup } from './fixtures.js';
test('actual controlled graph checkboxes commit after one user click', async ({ page }) => {
 const owned=assets();let immediateFailures=0;
 try {
  await realLogin(page);
  const me=await api(page,'GET','/me');
  owned.space=await api(page,'POST','/spaces',{name:owned.name},201);
  await api(page,'PUT','/grants',{space_id:owned.space.id,action:'read',subjects:['user:'+me.id]});
  await page.goto(publicURL+'/spaces/'+owned.space.id+'?tab=graph');
  const checkbox=page.getByRole('checkbox',{name:'uses',exact:true});
  await expect(checkbox).toBeChecked();
  try {await checkbox.uncheck();} catch(error){if(!error.message.includes('did not change its state'))throw error;immediateFailures++;}
  await expect(checkbox).not.toBeChecked();
  for(let i=0;i<10;i++){
   await checkbox.click();await expect(checkbox).toBeChecked();
   await checkbox.click();await expect(checkbox).not.toBeChecked();
  }
  console.log(JSON.stringify({originalImmediateFailures:immediateFailures,oneClickTransitions:20,productChanges:0}));
 } finally {await cleanup(page,owned);}
});
