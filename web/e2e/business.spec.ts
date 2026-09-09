import { test, expect } from '@playwright/test';
const permissions=['engagement:write','commerce:write','student:read','classgroup:assign','lesson:read','course:read'];
test.beforeEach(async({page})=>{
 await page.addInitScript((permissions)=>localStorage.setItem('eduze-auth',JSON.stringify({state:{user:{id:1,name:'校长',username:'admin',roles:['SUPER_ADMIN'],permissions,branches:[{id:1,name:'南山校区'}]},accessToken:'test',refreshToken:'refresh',branchIds:[1],permissions,sidebarCollapsed:false},version:0})),permissions);
 await page.route('**/api/**',route=>route.fulfill({json:{code:0,data:[]}}));
});
test('delivery fulfillment displays persisted address and verified reconciliation action',async({page})=>{
 const order={id:'o1',branchId:'1',productTitle:'水彩材料包',productType:'PHYSICAL',quantity:1,totalMinor:2990,paymentStatus:'PAID',fulfillmentStatus:'READY',fulfillmentMethod:'DELIVERY',recipientName:'李家长',recipientPhone:'13800138000',address:'南山区创作路 8 号',createdAt:'2026-09-08T09:00:00Z'};
 await page.route('**/api/v1/commerce/orders?*',route=>route.fulfill({json:{code:0,data:[order]}}));
 await page.route('**/api/v1/commerce/orders/o1',route=>route.fulfill({json:{code:0,data:order}}));
 await page.route('**/api/v1/commerce/dashboard?*',route=>route.fulfill({json:{code:0,data:{paidMinor:2990,refundedMinor:0,netReceiptsMinor:2990,definition:'已支付减已完成退款'}}}));
 await page.goto('/commerce');await page.getByLabel('校区',{exact:true}).selectOption('1');
 await expect(page.getByRole('link',{name:'商城与订单',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'查看 / 处理'}).click();
 await expect(page.getByText(/南山区创作路 8 号/)).toBeVisible();
 const request=page.waitForRequest(r=>r.url().endsWith('/commerce/orders/o1/reconcile')&&r.method()==='POST');
 await page.getByRole('button',{name:'主动对账'}).click();await request;
 await expect(page.getByRole('status')).toContainText('已向微信核实');
 await page.screenshot({path:'test-results/platform-commerce-delivery.png',fullPage:true});
});
test('enrolled lead cannot be downgraded or booked for another trial',async({page})=>{
 await page.route('**/api/v1/engagement/enquiries?*',route=>route.fulfill({json:{code:0,data:[{id:'e1',branchId:'1',studentName:'小明',phone:'13800138000',age:7,status:'ENROLLED',reservationId:'r1'}]}}));
 await page.route('**/api/**schedule**',route=>route.fulfill({json:{code:0,data:{days:[]}}}));
 await page.route('**/api/**class-groups**',route=>route.fulfill({json:{code:0,data:{items:[]}}}));
 await page.goto('/engagement');await page.getByLabel('校区',{exact:true}).selectOption('1');
 await page.getByRole('button',{name:'记录跟进 / 安排试听'}).click();
 await expect(page.getByLabel('跟进状态')).toHaveValue('ENROLLED');
 await expect(page.getByLabel('跟进状态')).toBeDisabled();
 await expect(page.getByRole('button',{name:'提交教务确认名额'})).toBeDisabled();
 await page.screenshot({path:'test-results/platform-engagement.png',fullPage:true});
});